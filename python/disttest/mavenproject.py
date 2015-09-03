import os
import logging
import fnmatch
import re

import classfile

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class Module:

    def __init__(self, root):
        self.root = root
        self.pom = os.path.join(root, "pom.xml")
        self.test_classes = []
        self.source_artifacts = []
        self.test_artifacts = []
        self.name = os.path.basename(self.root)

class NotMavenProjectException(Exception):
    pass

class ModuleNotFoundException(Exception):
    pass

class MavenProject:

    def __init__(self, project_root, include_modules=None, include_patterns=None, exclude_patterns=None):
        # Normalize the path
        if not project_root.endswith("/"):
            project_root += "/"
        # Validate some basic expectations
        if not os.path.isdir(project_root):
            raise NotMavenProjectException("Path " + project_root + "is not a directory!")
        if not os.path.isfile(os.path.join(project_root, "pom.xml")):
            raise NotMavenProjectException("No pom.xml file found in %s, is this a Maven project?" % project_root)
        self.project_root = project_root
        self.modules = [] # All modules in the project
        self.included_modules = [] # Modules that match the include_modules filter
        self.__include_modules = include_modules
        # Default filters to find test classes
        self.__filters = [PotentialTestClassNameFilter(), NoAbstractClassFilter()]
        # Additional user-specified include and exclude patterns
        # Prepend because these are likely more selective than the default filters
        if include_patterns is not None:
            include_filter = IncludePatternsFilter(include_patterns)
            self.__filters.insert(0, include_filter)
        if exclude_patterns is not None:
            exclude_filter = ExcludePatternsFilter(exclude_patterns)
            self.__filters.insert(0, exclude_filter)
        self._walk()

    def _walk(self):
        # Find the modules first, directories that have a pom.xml and a target dir
        for root, dirs, files in os.walk(self.project_root):
            if "pom.xml" in files and "target" in dirs:
                self.modules.append(Module(root))

        # If include_modules was specified, filter the found module list and check for missing modules
        self.included_modules = self.modules
        if self.__include_modules is not None:
            # Filter to just the specified modules
            self.included_modules = [m for m in self.modules if m.name in self.__include_modules]
            # Mismatch in length means we're missing some
            if len(self.included_modules) != len(self.__include_modules):
                for m in self.included_modules:
                    self.__include_modules.remove(m.name)
                assert len(self.__include_modules) > 0
                raise ModuleNotFoundException("Could not find specified modules: " + " ".join(self.__include_modules))

        # For each included module, look for test classes within target dir
        for module in self.included_modules:
            logger.debug("Traversing module %s", module.root)
            for root, dirs, files in os.walk(os.path.join(module.root, "target")):
                abs_files = [os.path.join(root, f) for f in files]
                # Make classfile objects for everything that's a valid class
                classfiles = self.__get_classfiles(abs_files)
                # Apply classfile filters
                for fil in self.__filters:
                    classfiles = [c for c in classfiles if fil.accept(c)]
                # Set module's classes to the filtered classfiles
                module.test_classes += classfiles

        # For each module, look for test-sources jars
        # These will later be extracted
        for module in self.modules:
            target_root = os.path.join(module.root, "target")
            for entry in os.listdir(target_root):
                abs_path = os.path.join(target_root, entry)
                if os.path.isfile(abs_path):
                    if entry.endswith("-test-sources.jar") or entry.endswith("-tests.jar"):
                        # Do not need test jars from a module if we're not running its tests
                        if module in self.included_modules:
                            module.test_artifacts.append(abs_path)
                    elif entry.endswith(".jar") and not entry.endswith("-sources.jar") and not entry.endswith("-javadoc.jar"):
                        module.source_artifacts.append(abs_path)

        num_modules = len(self.modules)
        num_classes = reduce(lambda x,y: x+y,\
                             [0] + [len(m.test_classes) for m in self.modules])
        logging.info("Found %s modules with %s test classes in %s",\
                     num_modules, num_classes, self.project_root)

    @staticmethod
    def __get_classfiles(files):
        classfiles = []
        for f in files:
            # Must be a file
            if not os.path.isfile(f):
                continue
            name = os.path.basename(f)
            # Only class files
            if not name.endswith(".class"):
                continue
            clazz = classfile.Classfile(f)
            classfiles.append(clazz)
        return classfiles


class ClassfileFilter:
    @staticmethod
    def accept(clazz):
        return True


class PotentialTestClassNameFilter(ClassfileFilter):
    @staticmethod
    def accept(clazz):
        f = os.path.basename(clazz.classfile)
        # No nested classes
        if "$" in f:
            return False
        # Must end in ".class". This is checked earlier, but be paranoid.
        if not f.endswith(".class"):
            return False
        # Match against default Surefire pattern
        name = f[:-len(".class")]
        if not name.startswith("Test") and \
        not name.endswith("Test") and \
        not name.endswith("TestCase"):
            return False
        return True


class NoAbstractClassFilter(ClassfileFilter):
    @staticmethod
    def accept(clazz):
        return not (clazz.is_interface() or clazz.is_abstract())

class IncludePatternsFilter(ClassfileFilter):
    def __init__(self, patterns = None):
        self.patterns = []
        self.__reobjs = []
        if patterns is not None:
            self.patterns = patterns
            regexes = [fnmatch.translate(p) for p in patterns]
            self.__reobjs = [re.compile(r) for r in regexes]

    def accept(self, clazz):
        matched = False
        for reobj in self.__reobjs:
            if reobj.match(clazz.classname) is not None:
                matched = True
                break
        return matched

class ExcludePatternsFilter(IncludePatternsFilter):
    def accept(self, clazz):
        """Exclude is the opposite of the include filter."""
        return not IncludePatternsFilter.accept(self, clazz)
