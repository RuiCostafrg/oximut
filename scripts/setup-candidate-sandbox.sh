#!/usr/bin/env bash
# Trusted provisioning only, on disposable native Ubuntu 24.04 CI runners.
set -euo pipefail
. /etc/os-release
[[ "$ID" == ubuntu && "$VERSION_ID" == 24.04 ]]
[[ "$(id -u)" != 0 ]]
# Both packages come from Ubuntu. Runner images also carry third-party APT
# sources that are unrelated to sandbox provisioning.
ubuntu_archive=(-o Dir::Etc::sourcelist=/etc/apt/sources.list.d/ubuntu.sources
                -o Dir::Etc::sourceparts=-)
sudo apt-get "${ubuntu_archive[@]}" update -qq
sudo apt-get "${ubuntu_archive[@]}" install -y bubblewrap apparmor
# Ubuntu's default userns policy denies namespace-local NET_ADMIN, breaking
# bwrap's loopback initialization. Allow userns for this system binary only.
# Do not disable AppArmor or its global unprivileged-userns restriction.
sudo tee /etc/apparmor.d/oximut-bwrap >/dev/null <<'PROFILE'
abi <abi/4.0>,
include <tunables/global>
profile oximut-bwrap /usr/bin/bwrap flags=(unconfined) {
  userns,
}
PROFILE
sudo apparmor_parser -r /etc/apparmor.d/oximut-bwrap
/usr/bin/bwrap --version
# Execute as the runner, never sudo. The required Python regressions exercise
# the actual production command, including network isolation and cap dropping.
