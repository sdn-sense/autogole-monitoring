source ../environment
rm -f prometheus.yml-new
echo "Starting new pod"
kubectl apply -f ../kubernetes/config-preparer.yaml --kubeconfig $KUBE_CONF -n $KUBE_NAMESPACE
while true; do
  if [ -f "prometheus.yml-new" ]; then
    break
  fi
  sleep 10
  kubectl cp autogole-monitoring-config-preparer:/root/prometheus.yml prometheus.yml-new --kubeconfig $KUBE_CONF -n $KUBE_NAMESPACE
  kubectl cp autogole-monitoring-config-preparer:/root/getConflog.log getConflog.log-new --kubeconfig $KUBE_CONF -n $KUBE_NAMESPACE
done

echo "The file is now available! Deleting Pod"
kubectl delete pod autogole-monitoring-config-preparer --kubeconfig $KUBE_CONF -n $KUBE_NAMESPACE

echo "To apply new config, please execute the following:"
echo "mv prometheus.yml-new prometheus.yml"
echo "kubectl create configmap prometheus-config --from-file=prometheus.yml=prometheus.yml --kubeconfig $KUBE_CONF -n $KUBE_NAMESPACE --dry-run=client -o yaml | kubectl apply --kubeconfig $KUBE_CONF -n $KUBE_NAMESPACE -f -"
echo "# And finally restart the prometheus pod."
