{{/*
SPDX-License-Identifier: Apache-2.0

Copyright [2026] [Cisco Systems, Inc. and its affiliates]

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
*/}}

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
