#!/bin/sh

export PATH=$PATH:`pwd`/.bin/

while :
do
  echo "========================================"
  echo "Checking USER Proxy"
  voms-proxy-init -bits 2048 -voms cms --valid 192:00 -cert ~/.globus/usercert.pem -key ~/.globus/userkey.pem -rfc
  voms-proxy-info -all

  export KUBECONFIG=~/.kube/config-ucsd
  kubectl create secret generic xcache-proxy \
      --save-config \
      --dry-run=client \
      --from-file=x509-proxy=/tmp/x509up_u0 \
      --namespace opennsa -o yaml | kubectl apply -f -

  echo "========================================"
  echo "Sleeping 3 hours before next update"
  sleep 10800
done
