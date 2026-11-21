// Minimal N-API addon for a Node "zygote" fork server.
//
// Compiled with gcc -shared -fPIC and loaded by Node. Node/libuv symbols are
// intentionally left undefined so they bind against the node binary at dlopen
// time (do NOT link with -z defs).
#include <node_api.h>
#include <uv.h>

#include <errno.h>
#include <sys/mman.h>
#include <stdio.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

// Build for the glibc Node that loads the addon. A bionic Node needs a bionic
// toolchain; mixing a glibc build into bionic Node is unsupported.

static napi_value ThrowErrno(napi_env env, const char* what, int err) {
  char buf[256];
  snprintf(buf, sizeof(buf), "%s: %s", what, strerror(err));
  napi_throw_error(env, NULL, buf);
  return NULL;
}

static napi_value GetInt(napi_env env, int64_t value) {
  napi_value out;
  if (napi_create_int64(env, value, &out) != napi_ok) return NULL;
  return out;
}

// V8 marks its heap reservations MADV_DONTFORK (VmFlags "dc"), so a plain
// fork() child would lose the JavaScript heap and fault on first use. Clear
// that flag on every mapping immediately before forking. Returns the number
// of ranges successfully re-marked.
#include <execinfo.h>
#include <signal.h>
static void DumpStack(int sig) {
  (void)sig;
  void* frames[64];
  int n = backtrace(frames, 64);
  backtrace_symbols_fd(frames, n, 2);
}

static int ReenableForkCopy(void) {
  FILE* maps = fopen("/proc/self/maps", "re");
  if (!maps) return -1;
  char line[512];
  int marked = 0;
  while (fgets(line, sizeof(line), maps)) {
    unsigned long start, end;
    if (sscanf(line, "%lx-%lx", &start, &end) != 2) continue;
    if (madvise((void*)start, end - start, MADV_DOFORK) == 0) marked++;
  }
  fclose(maps);
  return marked;
}

// fork(): returns the pid (0 in the child). In the child the libuv loop must
// be reinitialized before any further loop use.
static napi_value Fork(napi_env env, napi_callback_info info) {
  (void)info;
  if (ReenableForkCopy() < 0) return ThrowErrno(env, "maps", errno);
  pid_t pid = fork();
  if (pid < 0) return ThrowErrno(env, "fork", errno);
  if (pid == 0) {
    signal(SIGUSR2, DumpStack);  // diagnostic: kill -USR2 <pid> prints a stack
    int rc = uv_loop_fork(uv_default_loop());
    if (rc != 0) {
      const char* msg = "zygote: uv_loop_fork failed\n";
      ssize_t ignored = write(2, msg, strlen(msg));
      (void)ignored;
      _exit(127);
    }
  }
  return GetInt(env, (int64_t)pid);
}

// socketpair(): -> [fd0, fd1]
static napi_value Socketpair(napi_env env, napi_callback_info info) {
  (void)info;
  int fds[2];
  if (socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, fds) != 0)
    return ThrowErrno(env, "socketpair", errno);

  napi_value arr, a, b;
  if (napi_create_array_with_length(env, 2, &arr) != napi_ok) return NULL;
  if (napi_create_int32(env, fds[0], &a) != napi_ok) return NULL;
  if (napi_create_int32(env, fds[1], &b) != napi_ok) return NULL;
  napi_set_element(env, arr, 0, a);
  napi_set_element(env, arr, 1, b);
  return arr;
}

static int GetFdArg(napi_env env, napi_callback_info info, const char* name) {
  size_t argc = 1;
  napi_value argv[1];
  napi_get_cb_info(env, info, &argc, argv, NULL, NULL);
  if (argc < 1) {
    char buf[128];
    snprintf(buf, sizeof(buf), "%s: expected a file descriptor", name);
    napi_throw_type_error(env, NULL, buf);
    return -1;
  }
  int32_t fd = -1;
  if (napi_get_value_int32(env, argv[0], &fd) != napi_ok) {
    napi_throw_type_error(env, NULL, "fd must be a number");
    return -1;
  }
  return fd;
}

static napi_value CloseFd(napi_env env, napi_callback_info info) {
  int fd = GetFdArg(env, info, "closeFd");
  if (fd < 0) return NULL;
  if (close(fd) != 0) return ThrowErrno(env, "close", errno);
  napi_value undef;
  napi_get_undefined(env, &undef);
  return undef;
}

static napi_value ClearCloexec(napi_env env, napi_callback_info info) {
  int fd = GetFdArg(env, info, "clearCloexec");
  if (fd < 0) return NULL;
  int flags = fcntl(fd, F_GETFD);
  if (flags < 0) return ThrowErrno(env, "fcntl(F_GETFD)", errno);
  if (fcntl(fd, F_SETFD, flags & ~FD_CLOEXEC) < 0)
    return ThrowErrno(env, "fcntl(F_SETFD)", errno);
  napi_value undef;
  napi_get_undefined(env, &undef);
  return undef;
}

static napi_value DupFd(napi_env env, napi_callback_info info) {
  int fd = GetFdArg(env, info, "dupFd");
  if (fd < 0) return NULL;
  int duped = dup(fd);
  if (duped < 0) return ThrowErrno(env, "dup", errno);
  return GetInt(env, duped);
}

static void SetNamed(napi_env env, napi_value obj, const char* key,
                     napi_value value) {
  napi_set_named_property(env, obj, key, value);
}

static napi_value SetNamedInt(napi_env env, napi_value obj, const char* key,
                              int64_t value) {
  napi_value v = GetInt(env, value);
  if (v == NULL) return NULL;
  SetNamed(env, obj, key, v);
  return v;
}

// waitpidNohang(): -> {pid, code, signal} | null
static napi_value WaitpidNohang(napi_env env, napi_callback_info info) {
  (void)info;
  int status = 0;
  pid_t pid = waitpid(-1, &status, WNOHANG);
  if (pid == 0 || (pid < 0 && errno == ECHILD)) {
    napi_value null_value;
    napi_get_null(env, &null_value);
    return null_value;
  }
  if (pid < 0) return ThrowErrno(env, "waitpid", errno);

  napi_value obj, null_value;
  napi_create_object(env, &obj);
  napi_get_null(env, &null_value);
  SetNamedInt(env, obj, "pid", (int64_t)pid);
  if (WIFEXITED(status)) {
    SetNamedInt(env, obj, "code", (int64_t)WEXITSTATUS(status));
    SetNamed(env, obj, "signal", null_value);
  } else if (WIFSIGNALED(status)) {
    SetNamed(env, obj, "code", null_value);
    SetNamedInt(env, obj, "signal", (int64_t)WTERMSIG(status));
  } else {
    SetNamed(env, obj, "code", null_value);
    SetNamed(env, obj, "signal", null_value);
  }
  return obj;
}

static napi_value ExitNow(napi_env env, napi_callback_info info) {
  int code = GetFdArg(env, info, "_exitNow");
  if (code < 0 && code != 0) return NULL;
  _exit(code);
  return NULL;  // unreachable
}

static napi_value GetPid(napi_env env, napi_callback_info info) {
  (void)info;
  return GetInt(env, (int64_t)getpid());
}

static napi_value GetPpid(napi_env env, napi_callback_info info) {
  (void)info;
  return GetInt(env, (int64_t)getppid());
}

NAPI_MODULE_INIT() {
  napi_property_descriptor props[] = {
      {"fork", NULL, Fork, NULL, NULL, NULL, napi_default_jsproperty, NULL},
      {"socketpair", NULL, Socketpair, NULL, NULL, NULL,
       napi_default_jsproperty, NULL},
      {"closeFd", NULL, CloseFd, NULL, NULL, NULL, napi_default_jsproperty,
       NULL},
      {"clearCloexec", NULL, ClearCloexec, NULL, NULL, NULL,
       napi_default_jsproperty, NULL},
      {"dupFd", NULL, DupFd, NULL, NULL, NULL, napi_default_jsproperty, NULL},
      {"waitpidNohang", NULL, WaitpidNohang, NULL, NULL, NULL,
       napi_default_jsproperty, NULL},
      {"_exitNow", NULL, ExitNow, NULL, NULL, NULL, napi_default_jsproperty,
       NULL},
      {"getpid", NULL, GetPid, NULL, NULL, NULL, napi_default_jsproperty,
       NULL},
      {"getppid", NULL, GetPpid, NULL, NULL, NULL, napi_default_jsproperty,
       NULL},
  };
  if (napi_define_properties(env, exports,
                             sizeof(props) / sizeof(props[0]),
                             props) != napi_ok)
    return NULL;
  return exports;
}
