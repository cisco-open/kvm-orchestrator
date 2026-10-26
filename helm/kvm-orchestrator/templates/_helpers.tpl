{{/*
Common labels
*/}}
{{- define "kvm-orchestrator.labels" -}}
app.kubernetes.io/name: kvm-orchestrator
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels for a component
*/}}
{{- define "kvm-orchestrator.selectorLabels" -}}
app.kubernetes.io/name: kvm-orchestrator
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Full name. If the release is already named "kvm-orchestrator", don't double it up
(so `helm install kvm-orchestrator` yields clean kvm-orchestrator-* resource names).
*/}}
{{- define "kvm-orchestrator.fullname" -}}
{{- if contains "kvm-orchestrator" .Release.Name -}}
{{ .Release.Name }}
{{- else -}}
{{ .Release.Name }}-kvm-orchestrator
{{- end -}}
{{- end }}
