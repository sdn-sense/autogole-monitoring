source ../environment

kubectl create configmap prometheus-alerts \
  --from-file=alerts.yml=prometheus-alerts.yml \
  --kubeconfig $KUBE_CONF -n $KUBE_NAMESPACE --dry-run=client -o yaml | \
  kubectl apply --kubeconfig $KUBE_CONF -n $KUBE_NAMESPACE -f -


kubectl create configmap alertmanager-config \
  --from-file=alertmanager.yml=prometheus-alertmanager.yml \
  --kubeconfig $KUBE_CONF -n $KUBE_NAMESPACE --dry-run=client -o yaml | \
  kubectl apply --kubeconfig $KUBE_CONF -n $KUBE_NAMESPACE -f -
