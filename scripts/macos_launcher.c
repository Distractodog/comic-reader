/* Native launcher for Cover 2.0.app — runs bundled app code inside the .app. */
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static void parent_directory(const char *path, char *out, size_t out_size) {
    strncpy(out, path, out_size - 1);
    out[out_size - 1] = '\0';
    char *slash = strrchr(out, '/');
    if (slash != NULL) {
        *slash = '\0';
    }
}

static int resolve_executable_path(char *exe_path, size_t size) {
    uint32_t buf_size = (uint32_t)size;
    if (_NSGetExecutablePath(exe_path, &buf_size) != 0) {
        return -1;
    }

    char resolved[PATH_MAX];
    if (realpath(exe_path, resolved)) {
        strncpy(exe_path, resolved, size - 1);
        exe_path[size - 1] = '\0';
    }
    return 0;
}

static int launch_bundle(const char *boot_path) {
    char macos_dir[PATH_MAX];
    char bundle_python[PATH_MAX];
    char app_root_unresolved[PATH_MAX];
    char app_root[PATH_MAX];
    char main_py[PATH_MAX];
    char site_packages[PATH_MAX];
    char pythonpath[PATH_MAX];

    parent_directory(boot_path, macos_dir, sizeof(macos_dir));
    snprintf(bundle_python, sizeof(bundle_python), "%s/Cover 2.0", macos_dir);
    snprintf(app_root_unresolved, sizeof(app_root_unresolved), "%s/../Resources/app", macos_dir);

    if (!realpath(app_root_unresolved, app_root)) {
        fprintf(stderr, "Cover 2.0: bundled app files missing. Run scripts/setup_macos_app.sh\n");
        return 1;
    }

    snprintf(main_py, sizeof(main_py), "%s/src/main.py", app_root);
    snprintf(site_packages, sizeof(site_packages), "%s/site-packages", app_root);

    if (access(bundle_python, X_OK) != 0) {
        fprintf(stderr, "Cover 2.0: bundled runtime not found at %s\n", bundle_python);
        return 1;
    }
    if (access(main_py, R_OK) != 0) {
        fprintf(stderr, "Cover 2.0: bundled main.py not found at %s\n", main_py);
        return 1;
    }
    if (access(site_packages, R_OK) != 0) {
        fprintf(stderr, "Cover 2.0: bundled packages not found at %s\n", site_packages);
        return 1;
    }
    if (chdir(app_root) != 0) {
        perror("chdir");
        return 1;
    }

    snprintf(pythonpath, sizeof(pythonpath), "%s/src:%s", app_root, site_packages);
    setenv("COVER_LAUNCHED_FROM_APP", "1", 1);
    setenv("COVER_BUNDLED_LAUNCH", "1", 1);
    setenv("PYTHONPATH", pythonpath, 1);
    setenv("DYLD_LIBRARY_PATH", "/opt/homebrew/opt/expat/lib", 1);

    char *const new_argv[] = {bundle_python, main_py, NULL};
    execv(bundle_python, new_argv);
    perror("exec python");
    return 1;
}

int main(int argc, char **argv) {
    char boot_path[PATH_MAX];
    (void)argc;

    if (argv[0] && realpath(argv[0], boot_path)) {
        /* argv[0] is reliable when launched from a shell. */
    } else if (resolve_executable_path(boot_path, sizeof(boot_path)) != 0) {
        perror("executable path");
        return 1;
    }

    return launch_bundle(boot_path);
}
