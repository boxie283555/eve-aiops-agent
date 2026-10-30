ARG NAUTOBOT_VERSION=2.4.10
FROM networktocode/nautobot:${NAUTOBOT_VERSION}
USER root
RUN pip install --no-cache-dir 'napalm==5.0.0' 'netmiko==4.4.0' 'napalm-panos==0.7.0'
COPY aiops_napalm /usr/local/lib/python3.12/site-packages/aiops_napalm
USER nautobot
