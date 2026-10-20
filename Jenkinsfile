pipeline {
  agent any

  options {
    disableConcurrentBuilds()
  }

  // No webhook can reach this laptop, so poll GitHub every 2 minutes.
  triggers {
    pollSCM('H/2 * * * *')
  }

  environment {
    IMAGE = 'docker.io/dudakovict/docqa'
  }

  stages {
    stage('Checkout') {
      steps {
        checkout scm
        script {
          env.TAG = sh(script: 'git rev-parse --short=7 HEAD', returnStdout: true).trim()
          // Jenkins has no built-in [skip ci]; our own tag-bump commits carry it
          // so the pipeline does not build itself in a loop.
          env.SKIP = sh(script: "git log -1 --pretty=%B | grep -q '\\[skip ci\\]' && echo true || echo false",
                        returnStdout: true).trim()
        }
      }
    }

    // Unit tests run inside a Python container (Docker Pipeline plugin), the Jenkins
    // equivalent of GitHub Actions' `container:`: same locked dependency set as the image,
    // nothing installed on the agent, JUnit results published per build.
    stage('Test') {
      when { environment name: 'SKIP', value: 'false' }
      agent {
        docker {
          image 'ghcr.io/astral-sh/uv:0.13.4-python3.13-trixie-slim'
          // keep-id: rootless Podman maps the container's UID 1000 to the host's UID 1000,
          // so the stage container can write to the bind-mounted Jenkins workspace.
          args '--userns=keep-id -e HOME=/tmp -e UV_CACHE_DIR=/tmp/uv-cache -e UV_PROJECT_ENVIRONMENT=/tmp/venv -e UV_PYTHON_DOWNLOADS=never -e UV_LINK_MODE=copy'
          reuseNode true
        }
      }
      steps {
        dir('app') {
          sh 'uv sync --frozen --extra test && uv run --frozen pytest -q --junitxml=test-results.xml'
        }
      }
      post {
        always {
          junit 'app/test-results.xml'
        }
      }
    }

    stage('Build') {
      when { environment name: 'SKIP', value: 'false' }
      steps {
        sh "podman build --build-arg VERSION=${TAG} -t ${IMAGE}:${TAG} app/"
      }
    }

    // Gate: scan before push so a vulnerable image never reaches the registry.
    // --ignore-unfixed: only fail on CRITICALs that have a patched version available,
    // otherwise a CVE with no fix anywhere would block every deploy.
    stage('Scan') {
      when { environment name: 'SKIP', value: 'false' }
      steps {
        sh """
          trivy image --scanners vuln --severity HIGH,CRITICAL --ignore-unfixed \
            --format table ${IMAGE}:${TAG}
          trivy image --scanners vuln --severity CRITICAL --ignore-unfixed \
            --exit-code 1 --quiet ${IMAGE}:${TAG}
        """
      }
    }

    stage('Push') {
      when { environment name: 'SKIP', value: 'false' }
      steps {
        withCredentials([usernamePassword(credentialsId: 'dockerhub',
                                          usernameVariable: 'DH_USER',
                                          passwordVariable: 'DH_TOKEN')]) {
          sh 'echo "$DH_TOKEN" | podman login docker.io -u "$DH_USER" --password-stdin'
          sh "podman push ${IMAGE}:${TAG}"
          sh 'podman logout docker.io'
        }
      }
    }

    // GitOps handoff: the only thing CI changes in the cluster's desired state is
    // one line in the dev overlay. Argo CD sees the commit and does the deploy.
    stage('Update dev overlay') {
      when { environment name: 'SKIP', value: 'false' }
      steps {
        dir('k8s/overlays/dev') {
          sh "kustomize edit set image ${IMAGE}=${IMAGE}:${TAG}"
        }
        withCredentials([sshUserPrivateKey(credentialsId: 'github-deploy-key', keyFileVariable: 'SSH_KEY')]) {
          sh '''
            git config user.name  "jenkins"
            git config user.email "jenkins@localhost"
            git add k8s/overlays/dev/kustomization.yaml
            git commit -m "ci: deploy ${TAG} to dev [skip ci]"
            GIT_SSH_COMMAND="ssh -i $SSH_KEY -o IdentitiesOnly=yes" \
              git push git@github.com:dudakovict/docqa.git HEAD:main
          '''
        }
      }
    }
  }
}
