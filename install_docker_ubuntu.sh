set -e

export DEBIAN_FRONTEND=noninteractive

sudo apt-get remove docker docker-engine docker.io containerd runc || true

# Update the package list
sudo apt-get update -o Acquire::ForceIPv4=true || {
  # Retry with a clean apt state in case of transient mirror/proxy issues
  sudo rm -rf /var/lib/apt/lists/*
  sudo apt-get clean
  sudo apt-get update -o Acquire::ForceIPv4=true
}

# Install required packages
sudo apt-get install -y \
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

sudo apt-get update -o Acquire::ForceIPv4=true || {
  sudo rm -rf /var/lib/apt/lists/*
  sudo apt-get clean
  sudo apt-get update -o Acquire::ForceIPv4=true
}
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
