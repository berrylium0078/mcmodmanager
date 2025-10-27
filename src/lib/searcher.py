from src.lib.downloader import FileMetadata
import curseforge_api_wrapper as cfapi
from curseforge_api_wrapper.client import SortOrder
import modrinth_api_wrapper as mrapi
from typing import List, Optional, Tuple
from src.lib.version import MavenVersion

mod_loader_lookup = {
    "forge": 1,
    "cauldron": 2,
    "liteloader": 3,
    "fabric": 4,
    "quilt": 5,
    "neoforge": 6,
}

class ModSearcher:
    mc_version: str
    mod_loader: str
    mod_loader_id: int = 0
    curseforge_api_key: Optional[str]
    mrcli: mrapi.Client = mrapi.Client()
    cfcli: Optional[cfapi.Client] = None

    def __init__(self, mc_version: str, mod_loader: str, curseforge_api_key: Optional[str]):
        self.mc_version = mc_version
        self.mod_loader = mod_loader.lower()
        self.curseforge_api_key = curseforge_api_key
        if curseforge_api_key is not None:
            self.cfcli = cfapi.Client(curseforge_api_key)
            self.mod_loader_id = mod_loader_lookup.get(self.mod_loader, 0)

    def get_latest_versions(self, mods: List[mrapi.Project], releaseType: str) -> List[Tuple[mrapi.Project, mrapi.Version]]:
        if not mods:
            return []
        def filter_version_loader(x: mrapi.Project|mrapi.Version):
            if x.game_versions and not self.mc_version in x.game_versions:
                return False
            if x.loaders and not self.mod_loader in x.loaders:
                return False
            return True

        result:List[Tuple[mrapi.Project, mrapi.Version]] = []
        for pro in filter(filter_version_loader, mods):
            versions = filter(filter_version_loader, self.mrcli.list_project_versions(pro.id))
            versions = filter(lambda ver: (ver.version_type or "alpha") >= releaseType, versions)
            versions = sorted(versions, key=(lambda v: MavenVersion(v.version_number or "0.0.0")), reverse = True)
            versions = list(versions)
            if versions:
                result.append((pro, versions[0]))
        return result
    
    def search_modrinth(self, slugs: List[str], releaseType: Optional[str]) -> List[mrapi.Version]:
        result: List[mrapi.Version] = []
        releaseType = (releaseType or "alpha").lower()

        
        bucket_mod: set[str] = set()

        for (project, version) in self.get_latest_versions(self.mrcli.get_projects(slugs), releaseType):
            result.append(version)
            slugs.remove(project.slug)
            bucket_mod.add(project.id)

        # resolve dependencies
        bucket_ver: set[str] = set([ver.id for ver in result])
        versions = result
        while True:
            dependencies = [depver
                for version in versions
                for depver in version.dependencies or []
                if depver.dependency_type == "required"]

            depmod = set(modid for dep in dependencies
                if (modid := dep.project_id)
                if modid not in bucket_mod)
            depver = set(verid for dep in dependencies
                if (verid := dep.version_id)
                if verid not in bucket_ver)
            depmodver = self.get_latest_versions(
                    self.mrcli.get_projects(list(depmod)), releaseType
                    ) if depmod else []
            bucket_mod = bucket_mod.union(project.id for project, _ in depmodver)
            depver |= set(version.id for _, version in depmodver)
            if not depver:
                break
            versions = self.mrcli.get_versions(list(depver)) if depmod else []
            bucket_ver = bucket_ver.union(ver.id for ver in versions)
            result += versions

        # mark the primary file explicitly
        for version in result:
            if not any(file.primary for file in version.files):
                if version.files:
                    version.files[0].primary = True
        return result
        
    def get_latest_file(self, mod: cfapi.Mod, releaseTypeId: int):
        if not self.cfcli:
            return

        index = 0
        MAX_PAGE_SIZE = 50
        while True:
            response = self.cfcli.get_mod_files(
                modId = mod.id,
                gameVersion = self.mc_version,
                modLoaderType = self.mod_loader_id,
                index = index,
                pageSize = MAX_PAGE_SIZE)
            availableFiles = [file for file in response.data
                 if (file.releaseType or 3) <= releaseTypeId
                 if file.isAvailable]

            if availableFiles:
                file = availableFiles[0]
                break

            pagination = response.pagination
            index = pagination.index + pagination.resultCount
            if index == pagination.totalCount:
                return

        if not file.fileName:
            file.fileName = mod.slug + "-" + self.mod_loader + self.mc_version
        if not file.downloadUrl:
            file.downloadUrl = self.cfcli.get_file_download_url(mod.id, file.id)
        return file

    def search_curseforge(self, slugs: List[str], releaseType: Optional[str]) -> List[cfapi.File]:
        """Search for a mod on CurseForge"""
        if not self.cfcli:
            return []

        releaseType = (releaseType or "alpha").lower()
        releaseTypeId = { "release": 1, "beta": 2, "alpha": 3 }.get(releaseType, 0)

        modfiles = [(mod.slug, file) for slug in slugs
            if (modlist := self.cfcli.search_mods(
                    gameId = 432, # Minecraft
                    classId = 6, # Mods
                    gameVersion = self.mc_version,
                    modLoaderType = self.mod_loader_id,
                    slug = slug,
                    sortField=2,
                    sortOrder=SortOrder.Desc,
                    ).data)
            if (mod := modlist[0])
            if (file := self.get_latest_file(mod, releaseTypeId))]

        files: list[cfapi.File] = []
        bucket: set[str] = set()
        for slug,file in modfiles:
            slugs.remove(slug)
            files.append(file)
            bucket.add(slug)

        # resolve dependencies
        result = files
        while True:
            # being a set, duplicate elements are removed
            if not (depModIds := set(modid
                    for file in files
                    for dependency in file.dependencies or []
                    if dependency.relationType == 3 # only resolve required dependency
                    if (modid := dependency.modId) not in bucket)):
                break
            modfiles = [(mod.slug, file)
                for mod in self.cfcli.get_mods(list(depModIds))
                if (file := self.get_latest_file(mod, releaseTypeId))]
            for slug, file in modfiles:
                result.append(file)
                bucket.add(slug)
        return result

    def search_mods(self, slugs: List[str], releaseType: Optional[str]) -> List[FileMetadata]:
        # prefer modrinth API
        print('searching modrinth...')
        modrinth_mods = self.search_modrinth(slugs, releaseType)
        files_to_download = [
            FileMetadata(
                url = file.url,
                dest = file.filename,
                size = file.size,
                md5 = None,
                sha1 = file.hashes.sha1,
                sha512 = file.hashes.sha512)
            for ver in modrinth_mods
            for file in ver.files
            if file.primary
        ]
        if not slugs or not self.cfcli:
            return files_to_download

        print('Info: the following mods cannot be found on modrinth:')
        print(', '.join(slugs))
        print('searching on curseforge...')
        files_to_download += [
            FileMetadata(
                # will not be None, see get_latest_file()
                url = file.downloadUrl or "",
                # will not be None, see get_latest_file()
                dest = file.fileName or "",
                size = file.fileLength,
                sha1 = hashes.get(1),
                md5 = hashes.get(2),
                sha512 = None,
            )
            for file in self.search_curseforge(slugs, releaseType)
            if (hashes := { h.algo:h.value for h in file.hashes or [] }) or 1
        ]
        return files_to_download
