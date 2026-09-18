{{/*
Sanitise a redirector host+port string into a DNS/label-safe name.
  redir-12.t2-sense.ultralight.org:1094  ->  redir-12-t2-sense-ultralight-org-1094
Dots and colons become dashes; result is lowercased and truncated to 48 chars
so that "xrootd-mon-<name>" stays within the 63-char Kubernetes label limit.
*/}}
{{- define "xrootd-mon.endpointName" -}}
{{- $host := .host | replace "." "-" | replace ":" "-" | lower -}}
{{- $port := .port | toString -}}
{{- printf "%s-%s" $host $port | trunc 48 | trimSuffix "-" -}}
{{- end }}

{{/*
Full Deployment/Service name for an endpoint.
*/}}
{{- define "xrootd-mon.fullname" -}}
{{- printf "xrootd-mon-%s" (include "xrootd-mon.endpointName" .) | trunc 63 | trimSuffix "-" -}}
{{- end }}

{{/*
Service name for an endpoint (used as Prometheus scrape target DNS name).
*/}}
{{- define "xrootd-mon.serviceName" -}}
{{- printf "xrootd-mon-svc-%s" (include "xrootd-mon.endpointName" .) | trunc 63 | trimSuffix "-" -}}
{{- end }}

{{/*
Prometheus job name for an endpoint.
  site.name = T2_US_Caltech, host = redir-12.t2-sense.ultralight.org
  -> T2_US_Caltech_XRootD_redir-12
Only the first label of the hostname (before the first dot) is used.
*/}}
{{- define "xrootd-mon.jobName" -}}
{{- $shortHost := .host | splitList "." | first -}}
{{- printf "%s_XRootD_%s" .siteName $shortHost -}}
{{- end }}
