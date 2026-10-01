# kvmaestro v0.17.0 — K8s deployment runbook

Deploys the Helm chart alongside the running Docker stack on the rke2-k8s
cluster (10.0.0.0/24), preserving all data via `mysqldump` from Docker
into the new K8s MySQL, then stamping both databases to Alembic revision
`0001` so future upgrades apply cleanly.

Both stacks continue to run in parallel until DNS/UI cutover. Docker becomes
the dev sandbox after cutover; K8s becomes primary.

## Prerequisites

- kubectl context pointing at the rke2-k8s cluster.
- Docker stack still running on dev-host (source of truth for data).
- Registry `your-registry.example.com/kvmaestro/` has v0.17.0 tags for wvc-api,
  wvc-ui, wvc-console-proxy.
- DNS: `kvmaestro.example.com` resolves to the Istio ingress-gateway
  VIP (`kubectl -n istio-system get svc istio-ingressgateway` — External-IP).
- TLS cert imported as a Secret named `kvmaestro-tls` in the
  **istio-system** namespace (not the app namespace — Istio SDS reads TLS
  credentials from the same namespace as the ingress-gateway pod).
  Verify with:

      kubectl -n istio-system get secret kvmaestro-tls
      # If missing, import once your CA delivers the signed cert:
      # kubectl create secret tls kvmaestro-tls -n istio-system \
      #   --cert=fullchain.pem --key=kvmaestro.key

## 1. Build and push v0.17.0 images

Preferred path: run the three Jenkins pipelines on
`https://jenkins.example.com` — one per component image:

- `kvmaestro-api` — `controller/Jenkinsfile` (Alembic gate + build wvc-api)
- `kvmaestro-ui` — `frontend/Jenkinsfile` (build wvc-ui)
- `kvmaestro-console-proxy` — `console-proxy/Jenkinsfile` (build wvc-console-proxy)

All three run the version-consistency gate (all six version files agree)
before building. `controller/Jenkinsfile` additionally runs the Alembic
validation gate (throwaway MySQL runs `alembic upgrade head` end-to-end)
before pushing.

Manual fallback — build locally, tag for the registry, push. Do this on
dev-host where Docker is already set up.

    cd kvmaestro
    docker build -t your-registry.example.com/kvmaestro/wvc-api:v0.17.0 controller/
    docker build -t your-registry.example.com/kvmaestro/wvc-ui:v0.17.0 frontend/
    docker build -t your-registry.example.com/kvmaestro/wvc-console-proxy:v0.17.0 console-proxy/
    docker push your-registry.example.com/kvmaestro/wvc-api:v0.17.0
    docker push your-registry.example.com/kvmaestro/wvc-ui:v0.17.0
    docker push your-registry.example.com/kvmaestro/wvc-console-proxy:v0.17.0

Verify labels — each image should report `v0.17.0`:

    docker inspect your-registry.example.com/kvmaestro/wvc-api:v0.17.0 \
      --format '{{index .Config.Labels "org.opencontainers.image.version"}}'

## 2. Capture Docker DB as source of truth

Take a schema+data dump of the Docker MySQL. Preserve character set and
FKs by disabling FK checks around the load; single-transaction gives a
consistent snapshot without locking.

    docker exec kvmaestro-mysql sh -c \
      'mysqldump -u root -p"$MYSQL_ROOT_PASSWORD" \
        --single-transaction --set-gtid-purged=OFF \
        --default-character-set=utf8mb4 \
        --routines --events \
        kvmaestro' \
      > ./kvmaestro-v0.17.0-preinstall.sql

Sanity-check row counts before importing:

    docker exec kvmaestro-mysql sh -c \
      'mysql -u root -p"$MYSQL_ROOT_PASSWORD" -e \
        "SELECT COUNT(*) users FROM kvmaestro.users; \
         SELECT COUNT(*) vms FROM kvmaestro.vms; \
         SELECT COUNT(*) nodes FROM kvmaestro.nodes; \
         SELECT COUNT(*) tenants FROM kvmaestro.tenants;"'

Expect (as of 2026-07-02): 7 users, 28 VMs, 4 nodes, 6 tenants. Deviations
here mean the DB has moved on — re-take the dump.

## 3. Stamp the Docker DB to revision 0001

Before deploying K8s, register the Docker DB with Alembic so it's tracking
from the baseline. Alembic will create its `alembic_version` table and
insert `revision = '0001'` — no schema changes, just tracking.

    docker exec kvmaestro-api alembic stamp 0001
    docker exec kvmaestro-api alembic current    # should print: 0001 (head)

If Docker doesn't yet have the v0.17.0 image running, either pull first
(`docker pull ...:v0.17.0 && docker compose up -d api`) or run alembic from
outside the container against the running MySQL — same effect.

## 4. Install the chart

Create the namespace, install the chart in "workers=0" smoke-test posture.
The migration job is a Helm pre-install hook, so it runs before any pod
starts.

**Because the DB is empty when the job runs, the baseline migration
CREATE TABLE statements will execute.** That's what we want — it builds
the schema.

The `values.example.yaml` overlay has `istio.enabled: true` and
`istio.tls.enabled: true` from day one (the TLS Secret in istio-system
was set up during the prerequisites step). The chart's Gateway and
VirtualService will bind to `kvmaestro.example.com` on the shared
ingress-gateway pod immediately.

    kubectl create namespace kvmaestro
    helm install kvmaestro ./helm/kvmaestro \
      -n kvmaestro \
      -f ./helm/kvmaestro/values/values.example.yaml

Wait for the migrate job to finish (30-60s), then verify:

    kubectl -n kvmaestro get pods
    kubectl -n kvmaestro logs job/kvmaestro-migrate
    # expect: "Running upgrade  -> 0001, baseline v0.17.0"

Confirm the Istio manifests bound cleanly:

    kubectl -n kvmaestro get gateway,virtualservice
    # expect: gateway.networking.istio.io/kvmaestro-kvmaestro
    # expect: virtualservice.networking.istio.io/kvmaestro-kvmaestro

Confirm the schema is in place:

    MYSQL_POD=$(kubectl -n kvmaestro get pod -l app.kubernetes.io/component=mysql -o name)
    kubectl -n kvmaestro exec $MYSQL_POD -- \
      mysql -u root -pCHANGE_ME_DB_PASSWORD -e "SHOW TABLES FROM kvmaestro"
    # expect 9 tables: alembic_version + the 8 baseline tables

## 5. Import the Docker dump

Drop the empty tables the migration created, then load the dump. Wrapping
the load in `SET FOREIGN_KEY_CHECKS=0` handles any circular-FK edge cases
`mysqldump` may leave.

    MYSQL_POD=$(kubectl -n kvmaestro get pod -l app.kubernetes.io/component=mysql -o name)
    cat > /tmp/import-preamble.sql <<'EOF'
    SET FOREIGN_KEY_CHECKS=0;
    DROP TABLE IF EXISTS user_ssh_pubkeys, user_tenants, vms, nodes, networks, tenants, users, ssh_keys;
    EOF
    cat > /tmp/import-postamble.sql <<'EOF'
    SET FOREIGN_KEY_CHECKS=1;
    EOF
    cat /tmp/import-preamble.sql \
        ./kvmaestro-v0.17.0-preinstall.sql \
        /tmp/import-postamble.sql \
      | kubectl -n kvmaestro exec -i $MYSQL_POD -- \
          mysql -u root -pCHANGE_ME_DB_PASSWORD kvmaestro

Note the DROP intentionally omits `alembic_version` — we want to keep
Alembic's tracking row. If the dump happens to include an `alembic_version`
line (it won't; step 3 stamped Docker *after* the dump), the INSERT will
collide harmlessly and error non-fatally.

Row-count sanity check against step 2's numbers:

    kubectl -n kvmaestro exec $MYSQL_POD -- \
      mysql -u root -pCHANGE_ME_DB_PASSWORD -e \
        "SELECT COUNT(*) users FROM kvmaestro.users; \
         SELECT COUNT(*) vms FROM kvmaestro.vms; \
         SELECT COUNT(*) nodes FROM kvmaestro.nodes; \
         SELECT COUNT(*) tenants FROM kvmaestro.tenants;"

## 6. Stamp the K8s DB to revision 0001

The tables were dropped and reloaded, but Alembic's `alembic_version` row
was retained. Sanity-check:

    API_POD=$(kubectl -n kvmaestro get pod -l app.kubernetes.io/component=api -o name)
    kubectl -n kvmaestro exec $API_POD -- alembic current
    # expect: 0001 (head)

If the row was somehow lost, re-stamp:

    kubectl -n kvmaestro exec $API_POD -- alembic stamp 0001

## 7. Smoke test (HTTPS via Istio ingress-gateway)

The Istio ingress-gateway is shared cluster-wide; the External-IP shows in
istio-system. If DNS is not yet pointed at that VIP, use `--resolve` to
force curl to it.

    GW_IP=$(kubectl -n istio-system get svc istio-ingressgateway \
      -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
    curl --resolve kvmaestro.example.com:443:$GW_IP \
      https://kvmaestro.example.com/healthz
    # expect: {"status":"ok"}

Verify the HTTP→HTTPS 301 is also live:

    curl -I --resolve kvmaestro.example.com:80:$GW_IP \
      http://kvmaestro.example.com/
    # expect: HTTP/1.1 301 Moved Permanently
    # expect: location: https://kvmaestro.example.com/

Log in with a known LDAP user. VM/Node lists should match Docker.

## 8. TLS Secret rotation (reference only)

The TLS Secret was already imported into istio-system during the
prerequisites step, so no chart action is needed during a normal install.

If the cert ever needs to be rotated, replace the Secret in **istio-system**
(NOT in the app namespace) and Istio will pick it up via SDS within seconds
— no chart upgrade required:

    kubectl -n istio-system delete secret kvmaestro-tls
    kubectl -n istio-system create secret tls kvmaestro-tls \
      --cert=fullchain.pem --key=kvmaestro.key

If you need to bring the endpoint up briefly without TLS (for example a
plaintext smoke test before the cert is in place), edit
`values/values.example.yaml`:

    istio:
      tls:
        enabled: false
      httpRedirect: false

then `helm upgrade`. **Flip it back before real users hit the endpoint** —
LDAP bind passwords cross the wire in plaintext otherwise.

## 9. Cutover

At this point Docker and K8s run identical data. To make K8s primary:

1. Point DNS `kvmaestro.example.com` at the Istio ingress-gateway
   VIP (if it isn't already). The Docker stack keeps running under its old
   hostname `dev.example.com` as a dev sandbox.
2. Users who bookmarked dev-host keep working on Docker; users hitting
   the canonical hostname land on K8s.
3. Turn on workers + beat by setting `replicaCount.worker: 2` and
   `replicaCount.beat: 1` in the values file and `helm upgrade`. **Before
   doing this, shut down Docker's celery worker/beat** so the two stacks
   don't fight over the same libvirt handles.

## Rollback

The Docker stack is untouched throughout. If K8s misbehaves before workers
are enabled, delete the release and re-point DNS to Docker:

    helm uninstall kvmaestro -n kvmaestro
    kubectl delete namespace kvmaestro

The TLS Secret in istio-system is untouched by this — it lives outside the
app namespace on purpose. Leave it in place for the next install.

Docker continues to own the fleet. Debug at leisure and try again.

If K8s workers are enabled and something breaks, `helm rollback
kvmaestro 1 -n kvmaestro` reverts the manifests but does NOT
revert the DB. The DB is safe to roll back to the mysqldump from step 2
because no HA-related schema shipped in v0.17.

## Forward path (v0.18 preview)

v0.18 introduces HA and adds revision 0002+ carrying `ha_events` table,
`vms.ha_restart`, `vms.ha_priority`. Because everything is stamped at
0001 today, `helm upgrade` to v0.18 will apply 0001 → 0002 → 0003... as
a normal Alembic upgrade, no manual mysqldump/reload dance. That is
exactly what this v0.17.0 groundwork was for.
