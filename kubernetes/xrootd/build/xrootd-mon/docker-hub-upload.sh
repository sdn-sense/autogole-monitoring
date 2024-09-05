#!/bin/bash
set -x
TAG=dev
if [ $# -eq 1 ]
  then
    echo "Argument specified. Will use $1 to tag docker image"
    TAG=$1
fi

docker buildx build --platform=linux/amd64 -t xrootd-mon .

if [ $? -ne 0 ]; then
  echo "Docker build failed"
  exit 1
fi

docker login

# Docker MultiArch build is experimental and we faced
# few issues with building ppc64le on x86_64 machine (gcc, mariadb issue)
# So onyl for ppc64le - we have separate build which is done on ppc64le machine
today=`date +%Y%m%d`
docker tag xrootd-mon sdnsense/xrootd-mon:$TAG-$today
docker push sdnsense/xrootd-mon:$TAG-$today
docker tag xrootd-mon sdnsense/xrootd-mon:$TAG
docker push sdnsense/xrootd-mon:$TAG

