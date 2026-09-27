import plistlib
import subprocess
from types import SimpleNamespace
import pytest
import native_ai_launcher as launcher


def configure_gate(monkeypatch,entitlements=None):
    monkeypatch.setattr(launcher.platform,'system',lambda:'Darwin')
    monkeypatch.setattr(launcher,'protected_binary',lambda path:(1,2,3,4))
    calls=[]
    def run(args,**kwargs):
        calls.append((args,kwargs))
        if '--display' in args:
            return SimpleNamespace(returncode=0,stdout=plistlib.dumps(entitlements or {}))
        return SimpleNamespace(returncode=0,stdout=b'')
    monkeypatch.setattr(launcher.subprocess,'run',run)
    return calls


def test_gate_requires_pinned_team_identifier_and_embedded_entitlement(monkeypatch):
    calls=configure_gate(monkeypatch,{'com.apple.developer.endpoint-security.client':True})
    assert launcher.verify_installed_collector('ABCDEFGHIJ')==(1,2,3,4)
    requirement=next(value for value in calls[0][0] if value.startswith('-R='))
    assert 'anchor apple generic' in requirement and launcher.IDENTIFIER in requirement
    assert 'ABCDEFGHIJ' in requirement
    assert calls[0][1]['timeout']==10


@pytest.mark.parametrize('entitlements',[{}, {'com.apple.developer.endpoint-security.client':'true'},
    {'com.apple.developer.endpoint-security.client':True,'com.apple.security.get-task-allow':True}])
def test_bad_entitlements_do_not_pass(entitlements,monkeypatch):
    configure_gate(monkeypatch,entitlements)
    with pytest.raises(launcher.NativeLaunchError):launcher.verify_installed_collector('ABCDEFGHIJ')


def test_failed_signature_or_timeout_never_launches(monkeypatch):
    configure_gate(monkeypatch,{'com.apple.developer.endpoint-security.client':True})
    monkeypatch.setattr(launcher.os,'geteuid',lambda:0)
    def forbidden(*args,**kwargs):pytest.fail('must not launch')
    monkeypatch.setattr(launcher.subprocess,'Popen',forbidden)
    monkeypatch.setattr(launcher.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=1,stdout=b''))
    with pytest.raises(launcher.NativeLaunchError):launcher.launch_installed_collector('ABCDEFGHIJ')
    def timeout(*args,**kwargs):raise subprocess.TimeoutExpired('fixture',10)
    monkeypatch.setattr(launcher.subprocess,'run',timeout)
    with pytest.raises(launcher.NativeLaunchError):launcher.launch_installed_collector('ABCDEFGHIJ')


def test_no_team_or_administrator_service_never_launches(monkeypatch):
    monkeypatch.setattr(launcher.os,'geteuid',lambda:501)
    with pytest.raises(launcher.NativeLaunchError):launcher.launch_installed_collector('ABCDEFGHIJ')
    monkeypatch.setattr(launcher.platform,'system',lambda:'Darwin')
    with pytest.raises(launcher.NativeLaunchError):launcher.verify_installed_collector('invalid')


def test_symlink_and_user_owned_installation_are_rejected(tmp_path):
    binary=tmp_path/'collector';binary.write_bytes(b'fixture');binary.chmod(0o755)
    with pytest.raises(launcher.NativeLaunchError):launcher.protected_binary(binary)
    linked=tmp_path/'link';linked.symlink_to(binary)
    with pytest.raises(launcher.NativeLaunchError):launcher.protected_binary(linked)


def test_change_during_signature_checks_is_rejected(monkeypatch):
    configure_gate(monkeypatch,{'com.apple.developer.endpoint-security.client':True})
    identities=iter([(1,2,3,4),(1,2,3,5)])
    monkeypatch.setattr(launcher,'protected_binary',lambda path:next(identities))
    with pytest.raises(launcher.NativeLaunchError,match='发生变化'):launcher.verify_installed_collector('ABCDEFGHIJ')


def test_verified_launch_has_fixed_executable_no_shell_and_clean_environment(monkeypatch):
    configure_gate(monkeypatch,{'com.apple.developer.endpoint-security.client':True})
    monkeypatch.setattr(launcher.os,'geteuid',lambda:0)
    calls=[];process=object()
    def spawn(args,**kwargs):calls.append((args,kwargs));return process
    monkeypatch.setattr(launcher.subprocess,'Popen',spawn)
    assert launcher.launch_installed_collector('ABCDEFGHIJ') is process
    args,options=calls[0]
    assert args==[str(launcher.INSTALL_PATH)]
    assert options['close_fds'] is True and options['start_new_session'] is True
    assert options['env']=={'PATH':'/usr/bin:/bin','LANG':'C'}
    assert not options.get('shell')


def test_os_launch_failure_is_a_clear_unavailable_state(monkeypatch):
    configure_gate(monkeypatch,{'com.apple.developer.endpoint-security.client':True})
    monkeypatch.setattr(launcher.os,'geteuid',lambda:0)
    def failed(*args,**kwargs):raise OSError('fixture permission error')
    monkeypatch.setattr(launcher.subprocess,'Popen',failed)
    with pytest.raises(launcher.NativeLaunchError,match='未能启动'):launcher.launch_installed_collector('ABCDEFGHIJ')
