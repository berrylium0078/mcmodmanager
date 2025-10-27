#!/usr/bin/env python3
"""
Minecraft Mod Dependency Checker
Verifies if a list of Minecraft mod files have all dependencies resolved.
Supports: Forge, NeoForge, Fabric, Quilt, LiteLoader, Cauldron
"""

import argparse
import json
import zipfile
import toml
import tempfile
import shutil
import re
from pathlib import Path
from typing import Dict, List, Set, Tuple, Optional
from dataclasses import dataclass, field
from enum import Enum
from functools import total_ordering


class ModLoader(Enum):
    FORGE = "forge"
    NEOFORGE = "neoforge"
    FABRIC = "fabric"
    QUILT = "quilt"
    LITELOADER = "liteloader"
    CAULDRON = "cauldron"


# Special mod IDs that represent the game/loader itself
SPECIAL_MODS = {
    'minecraft',
    'forge',
    'neoforge',
    'fabricloader', 'fabric-loader', 'fabric',
    'quilt_loader', 'quilt',
    'liteloader',
    'cauldron',
    'java',
    'fml'  # Forge Mod Loader
}


@total_ordering
class MavenVersion:
    """
    Maven version implementation supporting proper version comparison.
    Handles versions like: 1.2.3, 1.2.3-alpha, 1.2-SNAPSHOT, etc.
    """
    
    def __init__(self, version_str: str):
        self.original = version_str
        self.parts = self._parse(version_str)
    
    def _parse(self, version_str: str):
        """Parse version into comparable parts"""
        # Split by dots and dashes
        parts = []
        for segment in re.split(r'[.\-]', version_str):
            if segment.isdigit():
                parts.append(('int', int(segment)))
            elif segment:
                # Known qualifiers have specific ordering
                segment_lower = segment.lower()
                if segment_lower in ['alpha', 'a']:
                    parts.append(('qualifier', 1, segment))
                elif segment_lower in ['beta', 'b']:
                    parts.append(('qualifier', 2, segment))
                elif segment_lower in ['milestone', 'm']:
                    parts.append(('qualifier', 3, segment))
                elif segment_lower in ['rc', 'cr']:
                    parts.append(('qualifier', 4, segment))
                elif segment_lower == 'snapshot':
                    parts.append(('qualifier', 5, segment))
                elif segment_lower == 'final' or segment_lower == 'ga':
                    parts.append(('qualifier', 6, segment))
                else:
                    parts.append(('str', segment.lower(), segment))
        return parts
    
    def __eq__(self, other):
        if not isinstance(other, MavenVersion):
            return False
        return self._compare(other) == 0
    
    def __lt__(self, other):
        if not isinstance(other, MavenVersion):
            return NotImplemented
        return self._compare(other) < 0
    
    def _compare(self, other):
        """Compare two versions"""
        for i in range(max(len(self.parts), len(other.parts))):
            p1 = self.parts[i] if i < len(self.parts) else ('int', 0)
            p2 = other.parts[i] if i < len(other.parts) else ('int', 0)
            
            # Compare by type first
            if p1[0] == 'int' and p2[0] == 'int':
                if p1[1] != p2[1]:
                    return p1[1] - p2[1]
            elif p1[0] == 'int' and p2[0] == 'qualifier':
                # Numbers before qualifiers
                return 1
            elif p1[0] == 'qualifier' and p2[0] == 'int':
                return -1
            elif p1[0] == 'qualifier' and p2[0] == 'qualifier':
                if p1[1] != p2[1]:
                    return p1[1] - p2[1]
                # Same qualifier level, compare strings
                if len(p1) > 2 and len(p2) > 2:
                    if p1[2] != p2[2]:
                        return -1 if p1[2] < p2[2] else 1
            elif p1[0] == 'str' or p2[0] == 'str':
                s1 = p1[1] if p1[0] == 'str' else str(p1[1])
                s2 = p2[1] if p2[0] == 'str' else str(p2[1])
                if s1 != s2:
                    return -1 if s1 < s2 else 1
        
        return 0
    
    def __str__(self):
        return self.original
    
    def __repr__(self):
        return f"MavenVersion('{self.original}')"
    
    def __hash__(self):
        return hash(self.original)


@dataclass
class VersionRange:
    """
    Represents a Maven version range.
    Syntax: [1.0,2.0) means >= 1.0 and < 2.0
            (1.0,2.0] means > 1.0 and <= 2.0
            1.0 means >= 1.0 (soft requirement)
    """
    intervals: List[Tuple[Optional[MavenVersion], bool, Optional[MavenVersion], bool]]  # (min, min_inclusive, max, max_inclusive)
    
    @staticmethod
    def parse(range_str: str) -> 'VersionRange':
        """Parse a Maven version range string"""
        range_str = range_str.strip()
        
        # Simple version (soft requirement >= version)
        if not any(c in range_str for c in '[](),'):
            return VersionRange([(MavenVersion(range_str), True, None, False)])
        
        intervals = []
        
        # Split by comma for multiple ranges
        for part in range_str.split(','):
            part = part.strip()
            if not part:
                continue
            
            # Parse interval notation
            if part.startswith('[') or part.startswith('('):
                min_inclusive = part[0] == '['
                part = part[1:]
                
                if part.endswith(']') or part.endswith(')'):
                    max_inclusive = part[-1] == ']'
                    part = part[:-1]
                else:
                    max_inclusive = False
                
                # Split the range
                versions = [v.strip() for v in part.split(',')]
                
                min_ver = MavenVersion(versions[0]) if versions[0] else None
                max_ver = MavenVersion(versions[1]) if len(versions) > 1 and versions[1] else None
                
                intervals.append((min_ver, min_inclusive, max_ver, max_inclusive))
        
        if not intervals:
            # Fallback: treat as soft requirement
            intervals.append((MavenVersion(range_str), True, None, False))
        
        return VersionRange(intervals)
    
    def contains(self, version: MavenVersion) -> bool:
        """Check if a version satisfies this range"""
        if not self.intervals:
            return True
        
        for min_ver, min_inc, max_ver, max_inc in self.intervals:
            # Check minimum
            if min_ver:
                if min_inc and version < min_ver:
                    continue
                if not min_inc and version <= min_ver:
                    continue
            
            # Check maximum
            if max_ver:
                if max_inc and version > max_ver:
                    continue
                if not max_inc and version >= max_ver:
                    continue
            
            # Passed all checks for this interval
            return True
        
        return False
    
    def intersect(self, other: 'VersionRange') -> 'VersionRange':
        """Compute intersection of two version ranges"""
        new_intervals = []
        
        for int1 in self.intervals:
            for int2 in other.intervals:
                # Compute intersection of two intervals
                min1, min_inc1, max1, max_inc1 = int1
                min2, min_inc2, max2, max_inc2 = int2
                
                # New minimum is the maximum of the two minimums
                if min1 is None:
                    new_min, new_min_inc = min2, min_inc2
                elif min2 is None:
                    new_min, new_min_inc = min1, min_inc1
                elif min1 > min2:
                    new_min, new_min_inc = min1, min_inc1
                elif min1 < min2:
                    new_min, new_min_inc = min2, min_inc2
                else:  # Equal
                    new_min = min1
                    new_min_inc = min_inc1 and min_inc2
                
                # New maximum is the minimum of the two maximums
                if max1 is None:
                    new_max, new_max_inc = max2, max_inc2
                elif max2 is None:
                    new_max, new_max_inc = max1, max_inc1
                elif max1 < max2:
                    new_max, new_max_inc = max1, max_inc1
                elif max1 > max2:
                    new_max, new_max_inc = max2, max_inc2
                else:  # Equal
                    new_max = max1
                    new_max_inc = max_inc1 and max_inc2
                
                # Check if interval is valid
                if new_min and new_max:
                    if new_min > new_max:
                        continue  # Empty intersection
                    if new_min == new_max and not (new_min_inc and new_max_inc):
                        continue  # Empty intersection
                
                new_intervals.append((new_min, new_min_inc, new_max, new_max_inc))
        
        return VersionRange(new_intervals)
    
    def __str__(self):
        """Convert back to string representation"""
        if not self.intervals:
            return "*"
        
        parts = []
        for min_ver, min_inc, max_ver, max_inc in self.intervals:
            if min_ver is None and max_ver is None:
                parts.append("*")
            elif max_ver is None:
                # Open-ended range
                parts.append(f"[{min_ver},)" if min_inc else f"({min_ver},)")
            elif min_ver is None:
                # Only upper bound
                parts.append(f"(,{max_ver}]" if max_inc else f"(,{max_ver})")
            else:
                left = '[' if min_inc else '('
                right = ']' if max_inc else ')'
                parts.append(f"{left}{min_ver},{max_ver}{right}")
        
        return ",".join(parts)
    
    def is_empty(self) -> bool:
        """Check if this range is empty (no valid versions)"""
        return len(self.intervals) == 0


@dataclass
class Dependency:
    mod_id: str
    version_range: str = "*"
    mandatory: bool = True
    ordering: str = "NONE"
    is_special: bool = False


@dataclass
class ModInfo:
    mod_id: str
    name: str
    version: str
    loader: ModLoader
    dependencies: List[Dependency] = field(default_factory=list)
    provides: List[str] = field(default_factory=list)
    file_path: Path = None
    is_jar_in_jar: bool = False
    parent_mod: str = None


class ModParser:
    """Parse mod metadata from various mod loaders"""
    
    @staticmethod
    def parse_mod_file(file_path: Path, extract_nested: bool = True) -> List[ModInfo]:
        """
        Parse a mod JAR file and extract metadata.
        Returns a list of ModInfo (main mod + any nested Jar-in-Jar mods)
        """
        mods = []
        
        try:
            with zipfile.ZipFile(file_path, 'r') as jar:
                # Parse main mod
                main_mod = ModParser._parse_main_mod(jar, file_path)
                if main_mod:
                    mods.append(main_mod)
                
                # Extract and parse nested JARs if enabled
                if extract_nested:
                    nested_mods = ModParser._extract_jar_in_jar(jar, file_path, main_mod)
                    mods.extend(nested_mods)
                
        except Exception as e:
            print(f"Error parsing {file_path.name}: {e}")
            return []
        
        # If no mods parsed, try to extract from filename
        if not mods:
            fallback_mod = ModParser._parse_from_filename(file_path)
            if fallback_mod:
                mods.append(fallback_mod)
        
        return mods
    
    @staticmethod
    def _parse_from_filename(file_path: Path) -> Optional[ModInfo]:
        """
        Try to extract mod ID and version from filename.
        Common patterns: modid-version.jar, modid_version.jar, modid-mc1.19.2-version.jar
        """
        name = file_path.stem  # Remove .jar
        
        # Try common patterns
        patterns = [
            r'^(.+?)[-_](\d+(?:\.\d+)*(?:[-+].+?)?)$',  # modid-1.2.3 or modid_1.2.3
            r'^(.+?)[-_]mc\d+\.\d+(?:\.\d+)?[-_](\d+(?:\.\d+)*(?:[-+].+?)?)$',  # modid-mc1.19.2-1.2.3
            r'^(.+?)[-_](?:forge|fabric|quilt)[-_](\d+(?:\.\d+)*(?:[-+].+?)?)$',  # modid-forge-1.2.3
        ]
        
        for pattern in patterns:
            match = re.match(pattern, name)
            if match:
                mod_id = match.group(1).lower().replace('-', '_').replace(' ', '_')
                version = match.group(2)
                
                return ModInfo(
                    mod_id=mod_id,
                    name=match.group(1),
                    version=version,
                    loader=ModLoader.FORGE,  # Unknown, default to Forge
                    file_path=file_path
                )
        
        # Fallback: use entire filename as mod_id
        print(f"Warning: Could not parse version from filename: {file_path.name}")
        return ModInfo(
            mod_id=name.lower().replace('-', '_').replace(' ', '_'),
            name=name,
            version="unknown",
            loader=ModLoader.FORGE,
            file_path=file_path
        )
    
    @staticmethod
    def _parse_main_mod(jar: zipfile.ZipFile, file_path: Path) -> ModInfo:
        """Parse the main mod from a JAR file"""
        # Try Forge/NeoForge (mods.toml)
        if 'META-INF/mods.toml' in jar.namelist():
            return ModParser._parse_forge_neoforge(jar, file_path)
        
        # Try Fabric/Quilt (fabric.mod.json)
        if 'fabric.mod.json' in jar.namelist():
            return ModParser._parse_fabric_quilt(jar, file_path)
        
        # Try old Forge (mcmod.info)
        if 'mcmod.info' in jar.namelist():
            return ModParser._parse_old_forge(jar, file_path)
        
        # Try LiteLoader (litemod.json)
        if 'litemod.json' in jar.namelist():
            return ModParser._parse_liteloader(jar, file_path)
        
        return None
    
    @staticmethod
    def _extract_jar_in_jar(jar: zipfile.ZipFile, parent_path: Path, parent_mod: ModInfo) -> List[ModInfo]:
        """
        Extract and parse nested JAR files (Jar-in-Jar).
        Common locations:
        - META-INF/jarjar/ (Forge/NeoForge)
        - META-INF/jars/ (Fabric/Quilt)
        """
        nested_mods = []
        parent_name = parent_mod.name if parent_mod else parent_path.stem
        
        # Look for nested JARs in common locations
        nested_jar_paths = []
        for name in jar.namelist():
            if name.endswith('.jar') and (
                name.startswith('META-INF/jarjar/') or
                name.startswith('META-INF/jars/') or
                '/jars/' in name
            ):
                nested_jar_paths.append(name)
        
        if not nested_jar_paths:
            return nested_mods
        
        # Create temp directory for extraction
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            
            for nested_jar_path in nested_jar_paths:
                try:
                    # Extract nested JAR
                    jar.extract(nested_jar_path, temp_path)
                    extracted_path = temp_path / nested_jar_path
                    
                    # Parse the nested JAR (don't recursively extract more JARs)
                    nested_jar_mods = ModParser.parse_mod_file(extracted_path, extract_nested=False)
                    
                    for nested_mod in nested_jar_mods:
                        nested_mod.is_jar_in_jar = True
                        nested_mod.parent_mod = parent_name
                        nested_mod.file_path = parent_path  # Reference parent file
                        nested_mods.append(nested_mod)
                        
                except Exception as e:
                    print(f"Warning: Failed to parse nested JAR {nested_jar_path}: {e}")
        
        return nested_mods
    
    @staticmethod
    def _resolve_placeholder(value: str, jar: zipfile.ZipFile, file_path: Path) -> str:
        """
        Resolve placeholders like ${file.jarVersion} in metadata.
        """
        if not value or '${' not in value:
            return value
        
        # ${file.jarVersion} - extract from jar manifest
        if '${file.jarVersion}' in value:
            try:
                manifest = jar.read('META-INF/MANIFEST.MF').decode('utf-8')
                for line in manifest.split('\n'):
                    if line.startswith('Implementation-Version:'):
                        version = line.split(':', 1)[1].strip()
                        value = value.replace('${file.jarVersion}', version)
                        return value
            except:
                pass
            
            # Fallback: try to extract from filename
            match = re.search(r'[-_](\d+(?:\.\d+)*(?:[-+].+?)?)\.jar$', file_path.name)
            if match:
                value = value.replace('${file.jarVersion}', match.group(1))
        
        return value
    
    @staticmethod
    def _parse_forge_neoforge(jar: zipfile.ZipFile, file_path: Path) -> ModInfo:
        """Parse Forge/NeoForge mods.toml"""
        content = jar.read('META-INF/mods.toml').decode('utf-8')
        data = toml.loads(content)
        
        # Determine if it's NeoForge or Forge
        loader_str = data.get('loaderVersion', '')
        loader = ModLoader.NEOFORGE if 'neoforge' in loader_str.lower() else ModLoader.FORGE
        
        # Get first mod entry
        mods = data.get('mods', [])
        if not mods:
            return None
        
        mod = mods[0]
        mod_id = mod.get('modId', 'unknown')
        name = mod.get('displayName', mod_id)
        version = ModParser._resolve_placeholder(mod.get('version', '0.0.0'), jar, file_path)
        
        # Parse dependencies
        dependencies = []
        for dep in data.get('dependencies', {}).get(mod_id, []):
            dep_mod_id = dep.get('modId', '')
            is_special = dep_mod_id.lower() in SPECIAL_MODS
            version_range = ModParser._resolve_placeholder(dep.get('versionRange', '*'), jar, file_path)
            
            dependencies.append(Dependency(
                mod_id=dep_mod_id,
                version_range=version_range,
                mandatory=dep.get('mandatory', True),
                ordering=dep.get('ordering', 'NONE'),
                is_special=is_special
            ))
        
        return ModInfo(
            mod_id=mod_id,
            name=name,
            version=version,
            loader=loader,
            dependencies=dependencies,
            file_path=file_path
        )
    
    @staticmethod
    def _parse_fabric_quilt(jar: zipfile.ZipFile, file_path: Path) -> ModInfo:
        """Parse Fabric/Quilt fabric.mod.json"""
        content = jar.read('fabric.mod.json').decode('utf-8')
        data = json.loads(content)
        
        mod_id = data.get('id', 'unknown')
        name = data.get('name', mod_id)
        version = ModParser._resolve_placeholder(data.get('version', '0.0.0'), jar, file_path)
        
        # Check if it's Quilt (has quilt_loader in depends)
        depends = data.get('depends', {})
        loader = ModLoader.QUILT if 'quilt_loader' in depends else ModLoader.FABRIC
        
        # Parse dependencies
        dependencies = []
        for dep_id, dep_version in depends.items():
            is_special = dep_id.lower() in SPECIAL_MODS
            dependencies.append(Dependency(
                mod_id=dep_id,
                version_range=dep_version if isinstance(dep_version, str) else '*',
                mandatory=True,
                is_special=is_special
            ))
        
        # Parse provides
        provides = data.get('provides', [])
        
        return ModInfo(
            mod_id=mod_id,
            name=name,
            version=version,
            loader=loader,
            dependencies=dependencies,
            provides=provides,
            file_path=file_path
        )
    
    @staticmethod
    def _parse_old_forge(jar: zipfile.ZipFile, file_path: Path) -> ModInfo:
        """Parse old Forge mcmod.info"""
        content = jar.read('mcmod.info').decode('utf-8')
        data = json.loads(content)
        
        if isinstance(data, list):
            data = data[0]
        
        mod_id = data.get('modid', 'unknown')
        name = data.get('name', mod_id)
        version = ModParser._resolve_placeholder(data.get('version', '0.0.0'), jar, file_path)
        
        # Parse dependencies
        dependencies = []
        for dep in data.get('requiredMods', []):
            is_special = dep.lower() in SPECIAL_MODS
            dependencies.append(Dependency(
                mod_id=dep,
                mandatory=True,
                is_special=is_special
            ))
        
        return ModInfo(
            mod_id=mod_id,
            name=name,
            version=version,
            loader=ModLoader.FORGE,
            dependencies=dependencies,
            file_path=file_path
        )
    
    @staticmethod
    def _parse_liteloader(jar: zipfile.ZipFile, file_path: Path) -> ModInfo:
        """Parse LiteLoader litemod.json"""
        content = jar.read('litemod.json').decode('utf-8')
        data = json.loads(content)
        
        mod_id = data.get('name', 'unknown')
        name = data.get('displayName', mod_id)
        version = ModParser._resolve_placeholder(data.get('version', '0.0.0'), jar, file_path)
        
        # LiteLoader doesn't typically have complex dependency definitions
        dependencies = []
        for dep in data.get('requiredMods', []):
            is_special = dep.lower() in SPECIAL_MODS
            dependencies.append(Dependency(
                mod_id=dep,
                mandatory=True,
                is_special=is_special
            ))
        
        return ModInfo(
            mod_id=mod_id,
            name=name,
            version=version,
            loader=ModLoader.LITELOADER,
            dependencies=dependencies,
            file_path=file_path
        )


class DependencyChecker:
    """Check if all mod dependencies are satisfied"""
    
    def __init__(self, mods: List[ModInfo]):
        self.mods = mods
        self.mod_map: Dict[str, ModInfo] = {}
        self.special_requirements: Dict[str, List[Tuple[str, str]]] = {}  # special_mod_id -> [(mod_name, version_range)]
        self._build_mod_map()
        self._collect_special_requirements()
    
    def _build_mod_map(self):
        """Build a map of mod_id -> ModInfo"""
        for mod in self.mods:
            self.mod_map[mod.mod_id] = mod
            # Also add provides
            for provided_id in mod.provides:
                self.mod_map[provided_id] = mod
    
    def _collect_special_requirements(self):
        """Collect all requirements for special mods (minecraft, forge, etc.)"""
        for mod in self.mods:
            for dep in mod.dependencies:
                if dep.is_special and dep.mandatory:
                    if dep.mod_id not in self.special_requirements:
                        self.special_requirements[dep.mod_id] = []
                    self.special_requirements[dep.mod_id].append((mod.name, dep.version_range))
    
    def check_dependencies(self) -> Tuple[bool, List[str]]:
        """
        Check if all dependencies are satisfied.
        Returns (all_satisfied, list_of_issues)
        """
        issues = []
        all_satisfied = True
        
        for mod in self.mods:
            for dep in mod.dependencies:
                if dep.is_special or not dep.mandatory:
                    continue  # Skip special mods and optional deps
                
                if dep.mod_id not in self.mod_map:
                    all_satisfied = False
                    issues.append(
                        f"❌ {mod.name} ({mod.mod_id}) requires {dep.mod_id} {dep.version_range} - NOT FOUND"
                    )
                else:
                    # Dependency found - check version
                    dep_mod = self.mod_map[dep.mod_id]
                    
                    try:
                        version_range = VersionRange.parse(dep.version_range)
                        dep_version = MavenVersion(dep_mod.version)
                        
                        if version_range.contains(dep_version):
                            issues.append(
                                f"✓ {mod.name} ({mod.mod_id}) requires {dep.mod_id} {dep.version_range} - found v{dep_mod.version}"
                            )
                        else:
                            all_satisfied = False
                            issues.append(
                                f"❌ {mod.name} ({mod.mod_id}) requires {dep.mod_id} {dep.version_range} - found v{dep_mod.version} (INCOMPATIBLE)"
                            )
                    except Exception as e:
                        # Fallback if version parsing fails
                        issues.append(
                            f"⚠️  {mod.name} ({mod.mod_id}) requires {dep.mod_id} {dep.version_range} - found v{dep_mod.version} (cannot verify)"
                        )
        
        return all_satisfied, issues
    
    def get_special_requirements_summary(self) -> List[str]:
        """
        Get a summary of special mod requirements (minecraft, forge, etc.)
        Shows the intersection of all version ranges required by mods.
        """
        summary = []
        
        for special_mod_id in sorted(self.special_requirements.keys()):
            requirements = self.special_requirements[special_mod_id]
            
            summary.append(f"\n{special_mod_id.upper()}:")
            
            # Compute intersection of all version ranges
            try:
                combined_range = None
                for mod_name, version_range_str in requirements:
                    version_range = VersionRange.parse(version_range_str)
                    if combined_range is None:
                        combined_range = version_range
                    else:
                        combined_range = combined_range.intersect(version_range)
                
                if combined_range and not combined_range.is_empty():
                    summary.append(f"  Required version: {combined_range}")
                else:
                    summary.append(f"  ❌ NO COMPATIBLE VERSION (conflicting requirements)")
                
                # Show individual requirements
                summary.append(f"  Individual requirements:")
                version_groups: Dict[str, List[str]] = {}
                for mod_name, version_range in requirements:
                    if version_range not in version_groups:
                        version_groups[version_range] = []
                    version_groups[version_range].append(mod_name)
                
                for version_range in sorted(version_groups.keys()):
                    mod_names = version_groups[version_range]
                    if len(mod_names) <= 3:
                        mods_str = ", ".join(mod_names)
                    else:
                        mods_str = f"{', '.join(mod_names[:3])} and {len(mod_names) - 3} more"
                    
                    summary.append(f"    {version_range} - {mods_str}")
                
                # If there are conflicts
                if combined_range and combined_range.is_empty():
                    summary.append(f"  ⚠️  CONFLICT: No version satisfies all requirements!")
                elif len(version_groups) > 1:
                    summary.append(f"  ℹ️  Multiple requirements - using intersection")
            
            except Exception as e:
                summary.append(f"  ⚠️  Error computing version intersection: {e}")
                # Fallback to simple listing
                for mod_name, version_range in requirements:
                    summary.append(f"    {version_range} - {mod_name}")
        
        return summary
    
    def get_missing_dependencies(self) -> Set[str]:
        """Get set of missing dependency mod IDs (excluding special mods)"""
        missing
