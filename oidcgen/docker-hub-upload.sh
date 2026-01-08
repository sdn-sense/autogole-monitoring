#!/bin/bash
set -x
TAG=latest
if [ $# -eq 1 ]
  then
    echo "Argument specified. Will use $1 to tag docker image"
    TAG=$1
fi

# Precheck that image is present (built recently
count=`docker images | grep oidcgen | grep latest | awk '{print $3}' | wc -l`
if [ "$count" -ne "1" ]; then
  echo "Count of docker images != 1. Which docker image you want to tag?"
  echo "Here is full list of docker images locally:"
  docker images | grep -i 'oidcgen\|REPOSITORY'
  echo "Please enter IMAGE ID:"
  read dockerimageid
else
  dockerimageid=`docker images | grep oidcgen | grep latest | awk '{print $3}'`
fi

docker login

today=`date +%Y%m%d`
docker tag $dockerimageid sdnsense/oidcgen:$TAG-$today
docker push sdnsense/oidcgen:$TAG-$today
docker tag $dockerimageid sdnsense/oidcgen:$TAG
docker push sdnsense/oidcgen:$TAG