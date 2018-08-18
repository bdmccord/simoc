FROM ubuntu:16.04

MAINTAINER Iurii Milovanov "duruku@gmail.com"

RUN apt-get update -y && \
    apt-get install -y python3-pip python3-dev python3-mysqldb

COPY . /app
WORKDIR /app

RUN pip3 install -r requirements.txt
RUN python3 create_db.py

ENTRYPOINT [ "/bin/bash" ]

CMD [ "run.sh" ]
