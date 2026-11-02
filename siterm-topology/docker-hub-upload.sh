#!/bin/bash
set -x
TAG=latest
if [ $# -eq 1 ]
  then
    echo "Argument specified. Will use $1 to tag docker image"
    TAG=$1
fi

# Push exactly the image build.sh produced (siterm-topology:latest). Grepping "siterm-topology" also
# matches the sdnsense/siterm-topology tags of earlier uploads and could push an old image.
dockerimageid=`docker images -q siterm-topology:latest`
if [ -z "$dockerimageid" ]; then
  echo "No local siterm-topology:latest image. Run ./build.sh first."
  exit 1
fi

docker login

today=`date +%Y%m%d`
docker tag $dockerimageid sdnsense/siterm-topology:$TAG-$today
docker push sdnsense/siterm-topology:$TAG-$today
docker tag $dockerimageid sdnsense/siterm-topology:$TAG
docker push sdnsense/siterm-topology:$TAG