from src.lib.downloader import FileMetadata
import curseforge_api_wrapper as cfapi
from curseforge_api_wrapper.client import SortOrder
import modrinth_api_wrapper as mrapi
from typing import List, Optional
from functools import reduce

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

    
    def search_modrinth(self, slugs: List[str], releaseType: Optional[str]) -> List[mrapi.Version]:
        result: List[mrapi.Version] = []
        releaseType = (releaseType or "alpha").lower()

        def filter_version_loader(x: mrapi.Project|mrapi.Version):
            if x.game_versions and not self.mc_version in x.game_versions:
                return False
            if x.loaders and not self.mod_loader in x.loaders:
                return False
            return True
        
        for pro in filter(filter_version_loader, self.mrcli.get_projects(slugs)):
            versions = filter(filter_version_loader, self.mrcli.list_project_versions(pro.id))
            versions = filter(lambda ver: (ver.version_type or "alpha") >= releaseType, versions)
            versions = sorted(versions, key=(lambda v: v.version_number))
            versions = list(versions)
            if versions:
                result.append(versions[0])
                slugs.remove(pro.slug)

        # resolve dependencies
        bucket: set[str] = set([ver.id for ver in result])
        versions = result
        while True:
            dependencies = map(lambda ver: ver.dependencies or [], versions)
            dependencies = reduce(lambda x, y: x + y, dependencies, [])
            dependencies = filter(lambda dep: dep.dependency_type == "required", dependencies)
            dependencies = map(lambda dep: dep.version_id or "", dependencies)
            dependencies = filter(lambda id: id not in bucket, dependencies)
            dependencies = list(set(dependencies))
            if not dependencies:
                break
            versions = self.mrcli.get_versions(dependencies)
            bucket = bucket.union(ver.id for ver in versions)
            result += versions
        return result
        
    def get_latest_file(self, mod: cfapi.Mod, releaseTypeId: int) -> Optional[cfapi.File]:
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
                    pageSize = MAX_PAGE_SIZE,
                )
            files = filter(lambda file: file.releaseType or 3 <= releaseTypeId, response.data)
            files = filter(lambda file: file.isAvailable, files)
            for file in files:
                if not file.fileName:
                    file.fileName = mod.slug + "-" + self.mod_loader + self.mc_version
                if not file.downloadUrl:
                    file.downloadUrl = self.cfcli.get_file_download_url(mod.id, file.id)
                return file
            pagination = response.pagination
            index = pagination.index + pagination.resultCount
            if index == pagination.totalCount:
                return

    def search_curseforge(self, slugs: List[str], releaseType: Optional[str]) -> List[cfapi.File]:
        """Search for a mod on CurseForge"""
        if not self.cfcli:
            return []

        releaseType = (releaseType or "alpha").lower()
        releaseTypeId = { "release": 1, "beta": 2, "alpha": 3 }.get(releaseType, 0)

        files: list[cfapi.File] = []
        foundMods = set()
        for slug in slugs:
            modlist = self.cfcli.search_mods(
                    gameId = 432,
                    gameVersion = self.mc_version,
                    modLoaderType = self.mod_loader_id,
                    slug = slug,
                    pageSize=1,
                    sortField=2,
                    sortOrder=SortOrder.Desc,
                ).data
            if modlist:
                file = self.get_latest_file(modlist[0], releaseTypeId)
                if file and file.isAvailable:
                    files.append(file)
                    foundMods.add(slug)
        for slug in foundMods:
            slugs.remove(slug)

        # resolve dependencies
        bucket: set[int] = set([file.modId for file in files])
        result = files
        while True:
            dependencies = map(lambda ver: ver.dependencies or [], files)
            dependencies = reduce(lambda x, y: x + y, dependencies, [])
            dependencies = filter(lambda dep: dep.relationType == 3, dependencies)
            dependencies = map(lambda dep: dep.modId, dependencies)
            dependencies = filter(lambda modId: modId not in bucket, dependencies)
            modIds : List[int] = list(set(dependencies))
            if not modIds:
                break
            modlist = self.cfcli.get_mods(modIds)
            files = []
            for mod in modlist:
                file = self.get_latest_file(mod, releaseTypeId)
                if file:
                    files.append(file)
                    bucket.add(mod.id)
            result += files
        return result

    def search_mods(self, slugs: List[str], releaseType: Optional[str]) -> List[FileMetadata]:
        # prefer modrinth API
        modrinth_mods = self.search_modrinth(slugs, releaseType)
        files_to_download = []
        for ver in modrinth_mods:
            for file in ver.files:
                files_to_download.append(FileMetadata(
                    url = file.url,
                    dest = file.filename,
                    size = file.size,
                    md5 = None,
                    sha1 = file.hashes.sha1,
                    sha512 = file.hashes.sha512,
                ))
        if slugs and self.cfcli:
            curseforge_mods = self.search_curseforge(slugs, releaseType)
            for file in curseforge_mods:
                hashes: dict[int, str] = {}
                for h in file.hashes or []:
                    hashes[h.algo] = h.value

                files_to_download.append(FileMetadata(
                    url = file.downloadUrl or "", # will not be None, see get_latest_file()
                    dest = file.fileName or "", # will not be None, see get_latest_file()
                    size = file.fileLength or -1,
                    sha1 = hashes.get(1),
                    md5 = hashes.get(2),
                    sha512 = None,
                ))
        return files_to_download
