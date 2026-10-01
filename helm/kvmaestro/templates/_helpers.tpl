{{/*
Common labels
*/}}
{{- define "kvmaestro.labels" -}}
app.kubernetes.io/name: kvmaestro
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels for a component
*/}}
{{- define "kvmaestro.selectorLabels" -}}
app.kubernetes.io/name: kvmaestro
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Full name. If the release is already named "kvmaestro", don't double it up
(so `helm install kvmaestro` yields clean kvmaestro-* resource names).
*/}}
{{- define "kvmaestro.fullname" -}}
{{- if contains "kvmaestro" .Release.Name -}}
{{ .Release.Name }}
{{- else -}}
{{ .Release.Name }}-kvmaestro
{{- end -}}
{{- end }}
