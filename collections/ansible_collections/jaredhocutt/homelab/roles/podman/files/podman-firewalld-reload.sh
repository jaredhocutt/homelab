#!/usr/bin/env bash

# https://github.com/containers/podman/issues/5431#issuecomment-1022121559

while read -r type timestamp serial sender destination path interface member _junk; do
    if [[ $type = '#'* ]]; then
        continue
    elif [[ $interface = org.freedesktop.DBus && $member = NameAcquired ]]; then
        echo "firewalld started"
        echo "reloading podman networks"
        podman network reload --all
        echo "podman networks reloaded"
    elif [[ $interface = org.fedoraproject.FirewallD1 && $member = Reloaded ]]; then
        echo "firewalld reloaded"
        echo "reloading podman networks"
        podman network reload --all
        echo "podman networks reloaded"
    fi
done < <(
    dbus-monitor \
        --profile \
        --system \
        "type=signal,sender=org.freedesktop.DBus,path=/org/freedesktop/DBus,interface=org.freedesktop.DBus,member=NameAcquired,arg0=org.fedoraproject.FirewallD1" \
        "type=signal,path=/org/fedoraproject/FirewallD1,interface=org.fedoraproject.FirewallD1,member=Reloaded" |
    sed -u "/^#/d"
)
