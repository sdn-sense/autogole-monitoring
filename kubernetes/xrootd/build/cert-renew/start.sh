#docker buildx build --platform=linux/amd64 -t cert-renew .

docker run \
  -dit --name cert-renew \
  -v /Users/jbalcas/.globus/:/root/.globus/ \
  -v  /Users/jbalcas/.kube/:/root/.kube/ \
  --restart always \
  --net=host \
  cert-renew
