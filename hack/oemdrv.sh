#!/usr/bin/env bash

set -eux

# Display current disks
echo "Current disks:"
diskutil list

# Prompt user for disk selection
echo ""
read -p "Enter the disk to partition (e.g., disk5): " disk_input

# Validate input is not empty
if [ -z "$disk_input" ]; then
    echo "Error: No disk specified"
    exit 1
fi

disk_path="/dev/${disk_input#/dev/}"
echo "Using disk: ${disk_path}"

diskutil partitionDisk ${disk_path} MBR FAT32 OEMDRV 10G "Free Space" DUMMY R
diskutil mountDisk OEMDRV

# Verify that /Volumes/OEMDRV exists
if [ ! -d "/Volumes/OEMDRV" ]; then
    echo "Error: /Volumes/OEMDRV does not exist"
    exit 1
fi

cp /tmp/ks.cfg /Volumes/OEMDRV/ks.cfg
sync

echo "Partitioning and file copy complete. Ejecting disk..."

diskutil eject ${disk_path}
