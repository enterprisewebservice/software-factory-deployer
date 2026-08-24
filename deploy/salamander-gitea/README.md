# Gitea on salamander (multi-user workshop testing)
Install: `oc apply -k https://github.com/rhpds/gitea-operator/OLMDeploy` (operator, cluster-wide),
then the Gitea CR below. The workload's gitea.yml stage configures users/orgs/tokens on top.
