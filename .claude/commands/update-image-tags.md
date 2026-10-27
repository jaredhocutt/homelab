Run the following command to determine which image tags need to be updated in the given inventory.

INVENTORY_NAME = "$ARGUMENTS"

If INVENTORY_NAME is blank, then prompt for which inventory to use. The options are the entries in the `inventory/host_vars/` directory: either a `<host>.yml` file or a `<host>/` directory holding one file per app (e.g. `apps`).

`uv run task check-image-tags inventory/host_vars/$INVENTORY_NAME`

The script accepts a file or a directory, with or without the `.yml` extension. Each update it reports includes a `File:` line with the path and line number of the variable. Edit that file, which for a split host is the app's own file, e.g. `inventory/host_vars/apps/karakeep.yml`.

- Never change a tag the script reports as **held**.
- For an update on a line with a `# hold: <url>` marker, set the tag to the reported latest value (the upstream pin), not the registry's newest tag, and keep the marker.
- Treat `*_version` lines with a `# github-release` marker the same way as image tags.
- If the script says a held tag should be re-checked after updating another variable, run it again after your edits and apply any new upstream pin.
- Skip any tag the script flags as not deployable (not pullable), and mention it in your output. After making changes to the inventory files, output the command needed to update the apps that require it.

The command should be in the following format with the `--tags` populated correctly. In a split host directory, each app's file is named after its role, which is also its tag.

`uv run ansible-playbook -v playbooks/$INVENTORY_NAME.yml --tags`

Then output a message that could be used for a git commit that outlines what was updated.
