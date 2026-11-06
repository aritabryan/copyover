"""Tray actions: native picker and notifications; never opens a browser."""
import json
import sys
import urllib.request
import threading
from functools import wraps

import dialogs
import p2p
import linked_pcs
import local_secret
from i18n import t

_pc_lock = threading.Lock()
_lock_management = threading.Lock()


def _pc_operation(function):
    @wraps(function)
    def run(*args, **kwargs):
        if not _pc_lock.acquire(blocking=False):
            notice('CopyOver', t('A connection or transfer is already in progress. Wait for the other PC’s request to finish.'))
            return
        try:
            return function(*args, **kwargs)
        finally:
            _pc_lock.release()
    return run


def notice(title, message):
    if sys.platform == 'win32':
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, title, 0x00010040)
    else:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        try:
            messagebox.showinfo(title, message, parent=root)
        finally:
            root.destroy()



def phone_destination(types) -> str:
    """Prepares for Android only if just Android devices are linked; with an iPhone or iPad, for iOS."""
    types = {kind for kind in types if kind}
    return 'android' if types and types <= {'android'} else 'ios'


def prepared_notice(title, message):
    """System notification (not a modal box) once the files are ready on the phone's side."""
    if sys.platform == 'win32':
        import windows_system as system
    else:
        import linux_system as system
    system.notify(title, message)


def send_phone(port, select=None, notify=None, linked_types=None):
    select = select or dialogs.select_native_files
    notify_error = notify or notice
    files = select()
    if not files:
        return
    destination = phone_destination(linked_types() if linked_types else [])
    data = json.dumps({'destination': destination, 'paths': [item['path'] for item in files]}).encode()
    request = urllib.request.Request(f'http://127.0.0.1:{int(port)}/app/send', data=data,
                                     headers={'Content-Type': 'application/json',
                                              local_secret.HEADER: local_secret.read()}, method='POST')
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read())
        if not result.get('ok'):
            raise ValueError('The PC refused the transfer.')
    except Exception:
        notify_error('CopyOver', t('Couldn’t prepare the transfer. Check that CopyOver is running and that the files don’t exceed 500 MiB.'))
        return
    (notify or prepared_notice)(t('File ready to receive'), result.get('message') or '')


def _choose_pc(pcs, notify, action):
    if not pcs:
        notify('CopyOver', t('No PCs nearby. Check that CopyOver is running on both and that they’re on the same network.'))
        return None
    return dialogs.choose_pc(pcs, action)


def _connect_selected(cfg, pc, save, notify, own_ca='', improve=False):
    """Pairs with the chosen PC if needed. `mejorar`: also when it was already linked without encryption."""
    peer_id = pc.get('id', '')
    if not peer_id:
        notify('CopyOver', t('Search for the computer again: its identifier is missing.'))
        return False
    info = cfg.get('pcs_info', {}).get(peer_id, {})
    if cfg.get('pcs', {}).get(peer_id) and (info.get('ca') or not improve):
        return True
    allow = cfg.get('allow_unencrypted_pairing', True) is not False and not info.get('ca')
    # The code comes out of the key exchange: the window opens once it's known, and even if it's rejected we
    # wait for the request to finish (the operation lock stays held until then).
    try:
        ok, _, token, remote_ca = p2p.pair_with_pc(
            ip=pc['ip'], port=int(pc['port']), my_name=cfg.get('name', 'PC'), my_id=cfg['id'],
            confirm_code=lambda code: dialogs.confirm_pairing(pc['name'], code),
            my_ca_pem=own_ca, expected_id=peer_id, allow_unencrypted=allow,
            announces_v2=str(pc.get('pairing', '')) == '2')
    except Exception:
        notify('CopyOver', t('Couldn’t complete the connection with the other PC.'))
        return False
    if not ok or not token:
        # A rejection by either person gives the usual notice; other reasons (like the other PC answering
        # without encryption even though it advertises it) are shown as they are.
        reason = token if not ok and token not in ('', t('Rejected'), t('Pairing rejected')) else ''
        notify('CopyOver', reason or t('The other PC didn’t confirm the connection. Try again and check both codes.'))
        return False
    save(peer_id, token, remote_ca)
    cfg.setdefault('pcs', {})[peer_id] = token
    if remote_ca:
        cfg.setdefault('pcs_info', {}).setdefault(peer_id, {})['ca'] = remote_ca
    else:
        notify(t('Unencrypted connection'), t('{name} has an older version of CopyOver, so transfers between the '
                                              'two will not be encrypted. Update it and link them again.', name=pc['name']))
    return True


@_pc_operation
def connect_pc(cfg, pcs, save, notify=None, own_ca=''):
    notify = notify or notice
    pc = _choose_pc(linked_pcs.with_alias(cfg, pcs), notify, 'link')
    if pc and _connect_selected(cfg, pc, save, notify, own_ca, improve=True):
        notify(t('PC connected'), t('{name} is paired. You can now use Send to another PC.', name=pc['name']))


@_pc_operation
def send_pc(cfg, pcs, send, save, notify=None, own_ca=''):
    notify = notify or notice
    pc = _choose_pc(linked_pcs.with_alias(cfg, pcs), notify, 'files')
    if not pc:
        return
    files = dialogs.select_native_files()
    if not files or not _connect_selected(cfg, pc, save, notify, own_ca):
        return
    try:
        send(cfg, ['--to', pc['id'], *[item['path'] for item in files]])
    except Exception:
        notify('CopyOver', t('Couldn’t send. Check that the other PC is still connected.'))


def manage_pcs(load, nearby, modify):
    if not _lock_management.acquire(blocking=False):
        return
    try:
        _manage_pcs(load, nearby, modify)
    finally:
        _lock_management.release()


def _manage_pcs(load, nearby, modify):
    while True:
        pcs = linked_pcs.list_all(load(), nearby())
        action = dialogs.manage_pcs(pcs)
        if not action:
            return
        try:
            modify(action['id'], action['action'], action.get('name', ''))
        except (ValueError, OSError) as error:
            notice(t('Linked computers'), str(error))
