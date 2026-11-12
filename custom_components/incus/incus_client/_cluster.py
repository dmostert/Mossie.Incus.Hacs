from __future__ import annotations

from . import models
from ._core import Core, E, Q
from .operations import Operation


class ClusterMixin(Core):
    def apply_server_preseed(self, config: models.InitPreseed) -> None:
        """Go: ApplyServerPreseed. Not ported (a large client-side orchestration routine in Go)."""
        raise NotImplementedError("apply_server_preseed is not implemented; call the individual create_*/update_* methods instead")

    # ---- certificates ----------------------------------------------------------------------
    def get_certificate_fingerprints(self) -> list[str]:
        return self._names("/certificates")

    def get_certificates(self) -> list[models.Certificate]:
        return self._get_list(models.Certificate, "/certificates", Q(("recursion", "1")))

    def get_certificate(self, fingerprint: str) -> tuple[models.Certificate, str]:
        return self._get_e(models.Certificate, f"/certificates/{E(fingerprint)}")

    def create_certificate(self, certificate: models.CertificatesPost) -> None:
        self._send("POST", "/certificates", certificate)

    def create_certificate_token(self, certificate: models.CertificatesPost) -> Operation:
        certificate.token = True
        return self._send_op("POST", "/certificates", certificate)

    def update_certificate(self, fingerprint: str, certificate: models.CertificatePut, etag: str) -> None:
        self._send("PUT", f"/certificates/{E(fingerprint)}", certificate, etag)

    def delete_certificate(self, fingerprint: str) -> None:
        self._send("DELETE", f"/certificates/{E(fingerprint)}")

    # ---- cluster ---------------------------------------------------------------------------
    def get_cluster(self) -> tuple[models.Cluster, str]:
        return self._get_e(models.Cluster, "/cluster")

    def update_cluster(self, cluster: models.ClusterPut, etag: str) -> Operation:
        return self._send_op("PUT", "/cluster", cluster, etag)

    def update_cluster_certificate(self, certs: models.ClusterCertificatePut, etag: str) -> None:
        self._send("PUT", "/cluster/certificate", certs, etag)

    def get_cluster_member_names(self) -> list[str]:
        return self._names("/cluster/members")

    def get_cluster_members(self) -> list[models.ClusterMember]:
        return self._get_list(models.ClusterMember, "/cluster/members", Q(("recursion", "1")))

    def get_cluster_member(self, name: str) -> tuple[models.ClusterMember, str]:
        return self._get_e(models.ClusterMember, f"/cluster/members/{E(name)}")

    def get_cluster_member_state(self, name: str) -> tuple[models.ClusterMemberState, str]:
        return self._get_e(models.ClusterMemberState, f"/cluster/members/{E(name)}/state")

    def create_cluster_member(self, member: models.ClusterMembersPost) -> Operation:
        return self._send_op("POST", "/cluster/members", member)

    def update_cluster_member(self, name: str, member: models.ClusterMemberPut, etag: str) -> None:
        self._send("PUT", f"/cluster/members/{E(name)}", member, etag)

    def update_cluster_member_state(self, name: str, state: models.ClusterMemberStatePost) -> Operation:
        return self._send_op("POST", f"/cluster/members/{E(name)}/state", state)

    def rename_cluster_member(self, name: str, member: models.ClusterMemberPost) -> None:
        self._send("POST", f"/cluster/members/{E(name)}", member)

    def delete_cluster_member(self, name: str, force: bool) -> None:
        self._send("DELETE", f"/cluster/members/{E(name)}", q=Q(("force", "1" if force else None)))

    def get_cluster_group_names(self) -> list[str]:
        return self._names("/cluster/groups")

    def get_cluster_groups(self) -> list[models.ClusterGroup]:
        return self._get_list(models.ClusterGroup, "/cluster/groups", Q(("recursion", "1")))

    def get_cluster_group(self, name: str) -> tuple[models.ClusterGroup, str]:
        return self._get_e(models.ClusterGroup, f"/cluster/groups/{E(name)}")

    def create_cluster_group(self, group: models.ClusterGroupsPost) -> None:
        self._send("POST", "/cluster/groups", group)

    def update_cluster_group(self, name: str, group: models.ClusterGroupPut, etag: str) -> None:
        self._send("PUT", f"/cluster/groups/{E(name)}", group, etag)

    def rename_cluster_group(self, name: str, group: models.ClusterGroupPost) -> None:
        self._send("POST", f"/cluster/groups/{E(name)}", group)

    def delete_cluster_group(self, name: str) -> None:
        self._send("DELETE", f"/cluster/groups/{E(name)}")

    # ---- profiles --------------------------------------------------------------------------
    def get_profile_names(self) -> list[str]:
        return self._names("/profiles")

    def get_profiles(self) -> list[models.Profile]:
        return self._get_list(models.Profile, "/profiles", Q(("recursion", "1")))

    def get_profile(self, name: str) -> tuple[models.Profile, str]:
        return self._get_e(models.Profile, f"/profiles/{E(name)}")

    def create_profile(self, profile: models.ProfilesPost) -> None:
        self._send("POST", "/profiles", profile)

    def update_profile(self, name: str, profile: models.ProfilePut, etag: str) -> None:
        self._send("PUT", f"/profiles/{E(name)}", profile, etag)

    def rename_profile(self, name: str, profile: models.ProfilePost) -> None:
        self._send("POST", f"/profiles/{E(name)}", profile)

    def delete_profile(self, name: str) -> None:
        self._send("DELETE", f"/profiles/{E(name)}")

    # ---- projects --------------------------------------------------------------------------
    def get_project_names(self) -> list[str]:
        return self._names("/projects")

    def get_projects(self) -> list[models.Project]:
        return self._get_list(models.Project, "/projects", Q(("recursion", "1")))

    def get_project(self, name: str) -> tuple[models.Project, str]:
        return self._get_e(models.Project, f"/projects/{E(name)}")

    def get_project_state(self, name: str) -> models.ProjectState:
        return self._get(models.ProjectState, f"/projects/{E(name)}/state")

    def create_project(self, project: models.ProjectsPost) -> None:
        self._send("POST", "/projects", project)

    def update_project(self, name: str, project: models.ProjectPut, etag: str) -> None:
        self._send("PUT", f"/projects/{E(name)}", project, etag)

    def rename_project(self, name: str, project: models.ProjectPost) -> Operation:
        return self._send_op("POST", f"/projects/{E(name)}", project)

    def delete_project(self, name: str) -> None:
        self._send("DELETE", f"/projects/{E(name)}")

    # ---- warnings --------------------------------------------------------------------------
    def get_warning_uuids(self) -> list[str]:
        return self._names("/warnings")

    def get_warnings(self) -> list[models.Warning]:
        return self._get_list(models.Warning, "/warnings", Q(("recursion", "1")))

    def get_warning(self, uuid: str) -> tuple[models.Warning, str]:
        return self._get_e(models.Warning, f"/warnings/{E(uuid)}")

    def update_warning(self, uuid: str, warning: models.WarningPut, etag: str) -> None:
        self._send("PUT", f"/warnings/{E(uuid)}", warning, etag)

    def delete_warning(self, uuid: str) -> None:
        self._send("DELETE", f"/warnings/{E(uuid)}")
