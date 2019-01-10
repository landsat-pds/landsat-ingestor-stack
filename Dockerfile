FROM ubuntu:16.04

RUN apt-get update
RUN apt-get -y install wget git parallel python python-pip software-properties-common
RUN add-apt-repository -y ppa:ubuntugis/ppa
RUN apt update
RUN apt-get -y install gdal-bin python-gdal
RUN pip install requests boto awscli usgs
RUN git clone https://github.com/landsat-pds/landsat_ingestor.git

ARG LANDSAT_PDS_ACCESS_ID
ENV AWS_ACCESS_KEY_ID=$LANDSAT_PDS_ACCESS_ID

ARG LANDSAT_PDS_ACCESS_KEY
ENV AWS_SECRET_ACCESS_KEY=$LANDSAT_PDS_ACCESS_KEY

ADD scripts/process-tarball /usr/local/bin/process-tarball
ENTRYPOINT ["/usr/local/bin/process-tarball"]