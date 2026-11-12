set -e

export DEBIAN_FRONTEND=noninteractive

# On a fresh VM, cloud-init can still be rewriting the apt mirror and lists
# while we use them; wait for it before touching apt.
cloud-init status --wait || true

# apt-get install does not take the lists lock, so a background update can
# delete the index files under it. Retry update and install together.
apt_install() {
  attempt=1
  until sudo apt-get update -o Acquire::ForceIPv4=true &&
    sudo apt-get install -y -o DPkg::Lock::Timeout=300 "$@"; do
    if [ "$attempt" -ge 5 ]; then
      echo "apt-get install $* failed after $attempt attempts" >&2
      return 1
    fi
    echo "apt-get failed (attempt $attempt), retrying in 15s with a clean apt state..." >&2
    attempt=$((attempt + 1))
    sudo rm -rf /var/lib/apt/lists/*
    sudo apt-get clean || true
    sleep 15
  done
}

sudo apt-get remove docker docker-engine docker.io containerd runc || true

apt_install \
    apt-transport-https \
    ca-certificates \
    curl \
    gnupg \
    lsb-release
# Add Docker’s official GPG key
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg
sudo chmod a+r /usr/share/keyrings/docker-archive-keyring.gpg
# Set up the stable repository
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu \
  $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

apt_install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
