// Notification-only Endpoint Security client. No argv, environment or file bodies.
#import <Foundation/Foundation.h>
#import <EndpointSecurity/EndpointSecurity.h>
#import <bsm/libbsm.h>
#import <libproc.h>
#import <sys/proc_info.h>
#import <stdatomic.h>
#import <signal.h>
#import <unistd.h>

static NSMutableDictionary<NSNumber *, NSDictionary *> *lineage;
static dispatch_queue_t writer;
static dispatch_semaphore_t capacity;
static dispatch_group_t outputGroup;
static dispatch_source_t stopSignals[2];
static _Atomic unsigned long dropped = 0;
static _Atomic unsigned long pending = 0, outputFailures = 0;
static _Atomic bool stopping = false;
static uint64_t lastSequence = 0, kernelDropped = 0;
static NSISO8601DateFormatter *clockFormat;
static BOOL syntheticMode = NO;
static NSLock *callbackLock;

static NSString *text(es_string_token_t token) {
    if (!token.data || !token.length) return @"";
    return [[NSString alloc] initWithBytes:token.data length:token.length encoding:NSUTF8StringEncoding] ?: @"";
}

static NSString *identify(NSString *path) {
    NSDictionary *apps = @{@"codex":@"Codex", @"chatgpt":@"ChatGPT", @"claude":@"Claude",
        @"cursor":@"Cursor", @"windsurf":@"Windsurf", @"ollama":@"Ollama", @"lm studio":@"LM Studio",
        @"lmstudio":@"LM Studio", @"jan":@"Jan", @"cherry studio":@"Cherry Studio",
        @"cherrystudio":@"Cherry Studio", @"trae":@"Trae", @"copilot":@"Copilot",
        @"aider":@"Aider", @"continue":@"Continue"};
    NSMutableArray *pieces = [NSMutableArray arrayWithObject:path.lastPathComponent.lowercaseString];
    for (NSString *part in path.pathComponents) if ([part.lowercaseString hasSuffix:@".app"])
        [pieces addObject:part.stringByDeletingPathExtension.lowercaseString];
    for (NSString *piece in pieces) for (NSString *key in apps)
        if ([piece isEqualToString:key] || [piece hasPrefix:[key stringByAppendingString:@" helper"]]) return apps[key];
    return nil;
}

static void emit(NSDictionary *event) {
    if (dispatch_semaphore_wait(capacity, DISPATCH_TIME_NOW)) { atomic_fetch_add(&dropped, 1); return; }
    NSMutableDictionary *copy = [event mutableCopy];
    copy[@"collector_dropped"] = @(atomic_load(&dropped));
    copy[@"kernel_dropped"] = @(kernelDropped);
    atomic_fetch_add(&pending, 1);
    dispatch_group_async(outputGroup, writer, ^{
        @autoreleasepool {
            NSData *json = [NSJSONSerialization dataWithJSONObject:copy options:NSJSONWritingSortedKeys error:nil];
            if (!json || fwrite(json.bytes, 1, json.length, stdout) != json.length
                || fputc('\n', stdout) == EOF || fflush(stdout) == EOF) atomic_fetch_add(&outputFailures, 1);
            atomic_fetch_sub(&pending, 1);
            dispatch_semaphore_signal(capacity);
        }
    });
}

static int drainOutput(void) {
    long timeout = dispatch_group_wait(outputGroup, dispatch_time(DISPATCH_TIME_NOW, 2 * NSEC_PER_SEC));
    unsigned long outstanding = atomic_load(&pending), failed = atomic_load(&outputFailures);
    fprintf(stderr,"Collector stopped: queue_dropped=%lu output_failed=%lu pending=%lu drain_timeout=%d\n",
        atomic_load(&dropped), failed, outstanding, timeout != 0);
    return failed || timeout ? 74 : 0;
}

static NSString *birth(struct timeval time) {
    return [NSString stringWithFormat:@"%lld.%06d", (long long)time.tv_sec, time.tv_usec];
}

static void seed() {
    int count = proc_listallpids(NULL, 0);
    if (count <= 0 || count > 65536) return;
    int slots = count + 128;
    pid_t *pids = calloc((size_t)slots, sizeof(pid_t));
    if (!pids) return;
    count = proc_listallpids(pids, slots * (int)sizeof(pid_t));
    if (count > slots) count = slots;
    NSMutableDictionary *processes = [NSMutableDictionary dictionary];
    for (int index = 0; index < count; index++) {
        char path[PROC_PIDPATHINFO_MAXSIZE] = {0};
        struct proc_bsdinfo info = {0};
        if (pids[index] <= 0 || pids[index] == getpid() || !proc_pidpath(pids[index], path, sizeof(path))) continue;
        if (proc_pidinfo(pids[index], PROC_PIDTBSDINFO, 0, &info, sizeof(info)) != sizeof(info)) continue;
        NSString *app = identify([NSString stringWithUTF8String:path]);
        processes[@(pids[index])] = [@{@"actor":app ?: @"", @"parent":@(info.pbi_ppid),
            @"birth":[NSString stringWithFormat:@"%llu.%06llu",info.pbi_start_tvsec,info.pbi_start_tvusec],
            @"method":app ? @"executable_match" : @"snapshot_lineage_bootstrap"} mutableCopy];
    }
    free(pids);
    BOOL changed = YES;
    while (changed) {
        changed = NO;
        for (NSNumber *pid in processes) {
            NSMutableDictionary *process = processes[pid];
            NSDictionary *parent = processes[process[@"parent"]];
            if (![process[@"actor"] length] && [parent[@"actor"] length]) { process[@"actor"] = parent[@"actor"]; changed = YES; }
        }
    }
    for (NSNumber *pid in processes) if ([processes[pid][@"actor"] length]) lineage[pid] = processes[pid];
}

static NSDictionary *attribute(const es_process_t *process, uint32_t version) {
    NSNumber *pid = @(audit_token_to_pid(process->audit_token));
    if (pid.intValue == getpid()) return nil;
    NSString *path = text(process->executable->path), *app = identify(path);
    int pidVersion = audit_token_to_pidversion(process->audit_token);
    NSDictionary *existing = lineage[pid];
    NSString *started = version >= 3 ? birth(process->start_time) : @"";
    NSString *method = @"executable_match";
    if (!app && existing && ((existing[@"pid_version"] && [existing[@"pid_version"] intValue] == pidVersion)
                            || (!existing[@"pid_version"] && started.length && [existing[@"birth"] isEqualToString:started]))) {
        app = existing[@"actor"]; method = existing[@"method"];
    }
    if (!app && version >= 4) {
        NSDictionary *parent = lineage[@(audit_token_to_pid(process->parent_audit_token))];
        if (parent[@"pid_version"] && [parent[@"pid_version"] intValue] == audit_token_to_pidversion(process->parent_audit_token)) {
            app = parent[@"actor"]; method = @"native_parent_chain";
        }
    }
    if (!app) { [lineage removeObjectForKey:pid]; return nil; }
    NSDictionary *identity = @{@"actor":app, @"method":method, @"birth":started, @"pid_version":@(pidVersion)};
    lineage[pid] = identity;
    return identity;
}

static void event(const es_message_t *message) {
    if (message->action_type != ES_ACTION_TYPE_NOTIFY) return;
    if (message->version >= 4) {
        if (lastSequence && message->global_seq_num > lastSequence + 1) kernelDropped += message->global_seq_num - lastSequence - 1;
        lastSequence = message->global_seq_num;
    }
    NSDictionary *identity = attribute(message->process, message->version);
    const es_process_t *process = message->process;
    NSString *kind = nil, *action = @"unknown", *path = @"", *destination = @"";
    NSString *source = @"filesystem", *status = @"observed";
    BOOL pathTruncated = NO, destinationTruncated = NO;
    BOOL modified = NO, mapped = NO;
    switch (message->event_type) {
        case ES_EVENT_TYPE_NOTIFY_EXEC: {
            const es_process_t *target = message->event.exec.target;
            NSDictionary *next = attribute(target, message->version);
            if (!next && identity) {
                lineage[@(audit_token_to_pid(target->audit_token))] = @{@"actor":identity[@"actor"], @"method":@"native_exec_chain",
                    @"pid_version":@(audit_token_to_pidversion(target->audit_token)), @"birth":message->version >= 3 ? birth(target->start_time) : @""};
                next = lineage[@(audit_token_to_pid(target->audit_token))];
            }
            identity = next; process = target; kind = @"native_exec"; source = @"process"; action = @"execute";
            path = text(target->executable->path); pathTruncated = target->executable->path_truncated; break;
        }
        case ES_EVENT_TYPE_NOTIFY_FORK: {
            const es_process_t *child = message->event.fork.child;
            if (identity) lineage[@(audit_token_to_pid(child->audit_token))] = @{@"actor":identity[@"actor"], @"method":@"native_parent_chain",
                @"pid_version":@(audit_token_to_pidversion(child->audit_token)), @"birth":message->version >= 3 ? birth(child->start_time) : @""};
            if (identity) identity = lineage[@(audit_token_to_pid(child->audit_token))];
            process = child; kind = @"native_fork"; source = @"process"; action = @"spawn"; break;
        }
        case ES_EVENT_TYPE_NOTIFY_EXIT: kind = @"native_exit"; source = @"process"; break;
        case ES_EVENT_TYPE_NOTIFY_OPEN: kind = @"native_open"; path = text(message->event.open.file->path); pathTruncated = message->event.open.file->path_truncated; break;
        case ES_EVENT_TYPE_NOTIFY_WRITE: kind = @"native_write"; path = text(message->event.write.target->path); pathTruncated = message->event.write.target->path_truncated; action = @"write"; break;
        case ES_EVENT_TYPE_NOTIFY_CLOSE:
            kind = @"native_close"; path = text(message->event.close.target->path); modified = message->event.close.modified;
            pathTruncated = message->event.close.target->path_truncated;
            mapped = message->version >= 6 && message->event.close.was_mapped_writable;
            break; // modified describes the file, not which process caused the modification.
        case ES_EVENT_TYPE_NOTIFY_UNLINK: kind = @"native_unlink"; path = text(message->event.unlink.target->path); pathTruncated = message->event.unlink.target->path_truncated; action = @"modify"; break;
        case ES_EVENT_TYPE_NOTIFY_RENAME:
            kind = @"native_rename"; path = text(message->event.rename.source->path); action = @"modify";
            pathTruncated = message->event.rename.source->path_truncated;
            destinationTruncated = message->event.rename.destination_type == ES_DESTINATION_TYPE_EXISTING_FILE
                ? message->event.rename.destination.existing_file->path_truncated : message->event.rename.destination.new_path.dir->path_truncated;
            destination = message->event.rename.destination_type == ES_DESTINATION_TYPE_EXISTING_FILE ? text(message->event.rename.destination.existing_file->path)
                : [text(message->event.rename.destination.new_path.dir->path) stringByAppendingPathComponent:text(message->event.rename.destination.new_path.filename)]; break;
        default: break;
    }
    if (identity && kind) {
        NSMutableDictionary *record = [@{@"schema":@"fieldwork-native-es/1", @"source_type":source,
            @"timestamp":[clockFormat stringFromDate:[NSDate dateWithTimeIntervalSince1970:message->time.tv_sec + message->time.tv_nsec / 1e9]],
            @"actor":identity[@"actor"], @"action_type":action, @"status":status, @"command_category":kind,
            @"synthetic":@(syntheticMode),
            @"path_truncated":@(pathTruncated), @"destination_truncated":@(destinationTruncated),
            @"executable_truncated":@(process->executable->path_truncated),
            @"process_id":@(audit_token_to_pid(process->audit_token)), @"parent_process_id":@(process->ppid),
            @"process_pid_version":@(audit_token_to_pidversion(process->audit_token)), @"process_started_at":identity[@"birth"],
            @"process_executable":text(process->executable->path).lastPathComponent,
            @"attribution_method":identity[@"method"], @"filesystem_path":[source isEqualToString:@"filesystem"] ? path : @"",
            @"resource":path.length ? path : text(process->executable->path).lastPathComponent,
            @"destination":destination, @"modified":@(modified), @"mapped_writable":@(mapped)} mutableCopy];
        if (message->version >= 4) record[@"native_sequence"] = @(message->global_seq_num);
        if (message->action.notify.result_type == ES_RESULT_TYPE_AUTH)
            record[@"native_authorization"] = message->action.notify.result.auth == ES_AUTH_RESULT_DENY ? @"denied" : @"allowed";
        else record[@"native_authorization"] = @"flags";
        if ([record[@"native_authorization"] isEqualToString:@"denied"]) record[@"status"] = @"blocked";
        emit(record);
    }
    if (message->event_type == ES_EVENT_TYPE_NOTIFY_EXIT) [lineage removeObjectForKey:@(audit_token_to_pid(message->process->audit_token))];
}

static int selfTest(void) {
    syntheticMode = YES;
    // Synthetic mode never subscribes to ES, and is visibly marked non-live.
    es_file_t file = {0}, executable = {0}; es_process_t process = {0}; es_message_t message = {0};
    const char *exe = "/Applications/Cursor.app/Contents/MacOS/Cursor", *path = "/tmp/fieldwork-fixture.txt";
    executable.path = (es_string_token_t){strlen(exe),exe}; file.path = (es_string_token_t){strlen(path),path};
    process.executable = &executable; process.audit_token.val[5] = 42; process.audit_token.val[7] = 7; process.ppid = 1;
    message.version = 6; message.action_type = ES_ACTION_TYPE_NOTIFY; message.process = &process; message.time.tv_sec = 1;
    message.event_type = ES_EVENT_TYPE_NOTIFY_CLOSE; message.event.close.target = &file;
    message.event.close.modified = false; message.event.close.was_mapped_writable = true; message.global_seq_num = 1;
    event(&message);
    message.event.close.modified = true; file.path_truncated = true; message.global_seq_num = 3; event(&message);
    fprintf(stderr,"SYNTHETIC SELF TEST: no live Endpoint Security collection\n");
    return drainOutput();
}

int main(int argc, const char **argv) {
    @autoreleasepool {
        lineage = [NSMutableDictionary dictionary]; writer = dispatch_queue_create("fieldwork.native.writer", DISPATCH_QUEUE_SERIAL);
        outputGroup = dispatch_group_create();
        signal(SIGPIPE, SIG_IGN);
        capacity = dispatch_semaphore_create(512); clockFormat = [NSISO8601DateFormatter new];
        callbackLock = [NSLock new];
        clockFormat.formatOptions = NSISO8601DateFormatWithInternetDateTime | NSISO8601DateFormatWithFractionalSeconds;
        if (argc == 2 && !strcmp(argv[1],"--self-test")) return selfTest();
        if (argc != 1) { fprintf(stderr,"Only --self-test is supported; no arbitrary commands are accepted.\n"); return 64; }
        es_client_t *client = NULL;
        es_new_client_result_t result = es_new_client(&client, ^(es_client_t *unused, const es_message_t *message) { (void)unused; @autoreleasepool { [callbackLock lock]; if (!atomic_load(&stopping)) event(message); [callbackLock unlock]; } });
        if (result != ES_NEW_CLIENT_RESULT_SUCCESS) {
            fprintf(stderr,"Endpoint Security inactive (result %d): requires Apple-approved signing, administrator privilege and Full Disk Access.\n",result); return 78;
        }
        seed();
        es_event_type_t types[] = {ES_EVENT_TYPE_NOTIFY_EXEC,ES_EVENT_TYPE_NOTIFY_FORK,ES_EVENT_TYPE_NOTIFY_EXIT,
            ES_EVENT_TYPE_NOTIFY_OPEN,ES_EVENT_TYPE_NOTIFY_WRITE,ES_EVENT_TYPE_NOTIFY_CLOSE,ES_EVENT_TYPE_NOTIFY_UNLINK,ES_EVENT_TYPE_NOTIFY_RENAME};
        if (es_subscribe(client,types,sizeof(types)/sizeof(types[0])) != ES_RETURN_SUCCESS) { es_delete_client(client); return 70; }
        int signals[] = {SIGTERM, SIGINT};
        for (int index = 0; index < 2; index++) {
            signal(signals[index], SIG_IGN);
            stopSignals[index] = dispatch_source_create(DISPATCH_SOURCE_TYPE_SIGNAL, signals[index], 0, dispatch_get_main_queue());
            dispatch_source_set_event_handler(stopSignals[index], ^{
                [callbackLock lock]; atomic_store(&stopping, true); [callbackLock unlock];
                es_unsubscribe_all(client);
                es_return_t deleted = es_delete_client(client);
                int status = drainOutput();
                exit(deleted == ES_RETURN_SUCCESS ? status : 70);
            });
            dispatch_resume(stopSignals[index]);
        }
        fprintf(stderr,"Endpoint Security active; metadata only, notification events only.\n");
        dispatch_main();
    }
}
