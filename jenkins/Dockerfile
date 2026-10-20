FROM docker.io/jenkins/jenkins:lts-jdk21

# Podman remote client only (no engine). It talks to the host's rootless Podman API socket,
# mounted at /run/podman/podman.sock, so builds and test containers run on the host.
# A `docker` shim keeps the Docker Pipeline plugin and Trivy working against the same socket.
ARG PODMAN_VERSION=5.7.0
ARG KUSTOMIZE_VERSION=5.8.3
ARG TRIVY_VERSION=0.76.0
USER root
RUN curl -fsSL https://github.com/containers/podman/releases/download/v${PODMAN_VERSION}/podman-remote-static-linux_amd64.tar.gz \
    | tar -xz -C /usr/local/bin --strip-components=1 bin/podman-remote-static-linux_amd64 \
 && mv /usr/local/bin/podman-remote-static-linux_amd64 /usr/local/bin/podman \
 && printf '#!/bin/sh\nexec /usr/local/bin/podman "$@"\n' > /usr/local/bin/docker && chmod +x /usr/local/bin/docker \
 && curl -fsSL https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize%2Fv${KUSTOMIZE_VERSION}/kustomize_v${KUSTOMIZE_VERSION}_linux_amd64.tar.gz \
    | tar -xz -C /usr/local/bin kustomize \
 && curl -fsSL https://github.com/aquasecurity/trivy/releases/download/v${TRIVY_VERSION}/trivy_${TRIVY_VERSION}_Linux-64bit.tar.gz \
    | tar -xz -C /usr/local/bin trivy \
 && ssh-keyscan -t ed25519 github.com >> /etc/ssh/ssh_known_hosts
USER jenkins

COPY plugins.txt /usr/share/jenkins/ref/plugins.txt
RUN jenkins-plugin-cli --plugin-file /usr/share/jenkins/ref/plugins.txt

# Skip the setup wizard; everything it would ask for comes from casc.yaml.
ENV JAVA_OPTS="-Djenkins.install.runSetupWizard=false"
ENV CASC_JENKINS_CONFIG=/usr/share/jenkins/casc.yaml
# Podman client and the Docker-compatible tools (Trivy, Docker Pipeline plugin) use the same socket.
ENV CONTAINER_HOST=unix:///run/podman/podman.sock DOCKER_HOST=unix:///run/podman/podman.sock
COPY casc.yaml /usr/share/jenkins/casc.yaml
