/* Native launcher for Cover 2.0.app.
 *
 * When the .app sits inside its source repo (the normal local-dev case) it runs
 * the LIVE src/ and venv directly, so code edits are reflected on the next launch
 * with no re-sync step. If the live tree is missing (e.g. the .app was copied out
 * standalone) it falls back to the bundled copy under Contents/Resources/app.
 */
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

/* Run Python with the given working dir, source root and site-packages.
 * Returns only on failure; on success it replaces this process via execv. */
static int run(const char *python, const char *cwd, const char *src_dir,
               const char *main_py, const char *site_packages, int live) {
    char pythonpath[2 * PATH_MAX];

    if (chdir(cwd) != 0) {
        perror("chdir");
        return 1;
    }

    snprintf(pythonpath, sizeof(pythonpath), "%s:%s", src_dir, site_packages);
    setenv("COVER_LAUNCHED_FROM_APP", "1", 1);
    setenv("COVER_BUNDLED_LAUNCH", live ? "0" : "1", 1);
    setenv("PYTHONPATH", pythonpath, 1);
    setenv("DYLD_LIBRARY_PATH", "/opt/homebrew/opt/expat/lib", 1);

    char *const new_argv[] = {(char *)python, (char *)main_py, NULL};
    execv(python, new_argv);
    perror("exec python");
    return 1;
}

static int launch(const char *boot_path) {
    char macos_dir[PATH_MAX];
    char bundle_python[PATH_MAX];

    parent_directory(boot_path, macos_dir, sizeof(macos_dir));
    snprintf(bundle_python, sizeof(bundle_python), "%s/Cover 2.0", macos_dir);

    if (access(bundle_python, X_OK) != 0) {
        fprintf(stderr, "Cover 2.0: bundled runtime not found at %s\n", bundle_python);
        return 1;
    }

    /* The .app is <repo>/Cover 2.0.app, so the repo root is three levels up
     * from Contents/MacOS. Prefer the live source tree there so edits are
     * always picked up without re-running setup_macos_app.sh. */
    char repo_unresolved[PATH_MAX];
    char repo_root[PATH_MAX];
    snprintf(repo_unresolved, sizeof(repo_unresolved), "%s/../../..", macos_dir);
    if (realpath(repo_unresolved, repo_root)) {
        char live_src[PATH_MAX];
        char live_main[PATH_MAX];
        char live_sp[PATH_MAX];
        snprintf(live_src, sizeof(live_src), "%s/src", repo_root);
        snprintf(live_main, sizeof(live_main), "%s/src/main.py", repo_root);
        snprintf(live_sp, sizeof(live_sp),
                 "%s/venv/lib/python3.12/site-packages", repo_root);

        if (access(live_main, R_OK) == 0 && access(live_sp, R_OK) == 0) {
            return run(bundle_python, repo_root, live_src, live_main, live_sp, 1);
        }
    }

    /* Fallback: bundled snapshot under Contents/Resources/app. */
    char app_unresolved[PATH_MAX];
    char app_root[PATH_MAX];
    snprintf(app_unresolved, sizeof(app_unresolved), "%s/../Resources/app", macos_dir);
    if (!realpath(app_unresolved, app_root)) {
        fprintf(stderr, "Cover 2.0: app files missing (live or bundled). Run scripts/setup_macos_app.sh\n");
        return 1;
    }

    char bundled_main[PATH_MAX];
    char bundled_src[PATH_MAX];
    char bundled_sp[PATH_MAX];
    snprintf(bundled_src, sizeof(bundled_src), "%s/src", app_root);
    snprintf(bundled_main, sizeof(bundled_main), "%s/src/main.py", app_root);
    snprintf(bundled_sp, sizeof(bundled_sp), "%s/site-packages", app_root);

    if (access(bundled_main, R_OK) != 0) {
        fprintf(stderr, "Cover 2.0: main.py not found at %s\n", bundled_main);
        return 1;
    }
    if (access(bundled_sp, R_OK) != 0) {
        fprintf(stderr, "Cover 2.0: bundled packages not found at %s\n", bundled_sp);
        return 1;
    }

    return run(bundle_python, app_root, bundled_src, bundled_main, bundled_sp, 0);
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

    return launch(boot_path);
}
