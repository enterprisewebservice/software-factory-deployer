# deploy-showroom

A simplified Showroom deployment role for field content demos.

This role is based on `agnosticd.showroom.ocp4_workload_showroom` but removes the `agnosticd.core` dependency, making it suitable for standalone deployments outside of the AgnosticD framework.

## What was removed

- **Multi-user support**: Only single-user deployments are supported
- **agnosticd.core.agnosticd_user_data lookup**: User data is passed directly via `showroom_user_data` variable
- **agnosticd.core.agnosticd_user_info reporting**: The calling playbook is responsible for creating userinfo ConfigMaps
- **Wetty terminal support**: Only the showroom terminal is supported
- **noVNC support**: VNC client deployment is not supported
- **Passthrough user data**: Not supported

## Requirements

- Ansible collection: `kubernetes.core`
- Helm CLI installed on the controller
- OpenShift cluster with admin access

## Required Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `showroom_guid` | Unique identifier for this deployment | `demo` |
| `showroom_deployer_domain` | Cluster apps domain | `apps.cluster.example.com` |
| `showroom_content_git_repo` | Git repository with Antora content | `https://github.com/rhpds/showroom_template_default.git` |

## Optional Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `showroom_namespace` | `showroom-{{ showroom_guid }}` | Namespace for deployment |
| `showroom_content_git_repo_ref` | `main` | Git branch/tag to use |
| `showroom_terminal_type` | `showroom` | Terminal type: `showroom` or empty |
| `showroom_user_data` | `{}` | Dictionary of data available in Antora templates |

## Output Variables

After the role completes, the following facts are set:

| Variable | Description |
|----------|-------------|
| `showroom_url` | The full URL to access Showroom |

## Example Usage

```yaml
- name: Deploy Showroom lab guide
  vars:
    showroom_guid: "{{ guid }}"
    showroom_deployer_domain: "{{ cluster_domain }}"
    showroom_content_git_repo: "https://github.com/myorg/my-lab-guide.git"
    showroom_user_data:
      web_console_url: "https://console.{{ cluster_domain }}"
      api_url: "https://api.cluster.example.com:6443"
  ansible.builtin.include_role:
    name: deploy-showroom

- name: Display showroom URL
  ansible.builtin.debug:
    msg: "Lab guide available at: {{ showroom_url }}"
```

## Upstream Source

This role is derived from:
- Repository: https://github.com/agnosticd/showroom
- Role: `roles/ocp4_workload_showroom`
- Helm Chart: https://github.com/rhpds/showroom-deployer
