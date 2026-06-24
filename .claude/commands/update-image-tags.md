Run the following command to determine which image tags need to be updated in the given inventory file.

INVENTORY_NAME = "$ARGUMENTS"

If INVENTORY_NAME is blank, then prompt for which inventory file to use. The file options can be found in the `inventory/host_vars/` directory.

`uv run task check-image-tags inventory/host_vars/$INVENTORY_NAME.yml`

Using the output of the command, make an update to the respective inventory file to update the image tags that need it. After making changes to the inventory file, run the command to run to update the apps that require it.

The command should be in the following format with the `--tags` populated correctly.

`uv run ansible-playbook -v playbooks/$INVENTORY_NAME.yml --tags`

Then output a message that could be used for a git commit that outlines what was updated. Then create a commmit using `git commit -sS` and use the generated commit message. Ask for approval before creating the commit.
