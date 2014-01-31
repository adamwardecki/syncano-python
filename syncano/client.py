import asyncore
import socket
import ssl
import time
import json
import logging

from syncano.exceptions import AuthException, ApiException, ConnectionLost


HOST = 'api.syncano.com'
PORT = 8200

logger = logging.getLogger('syncano.client')


class MessageHandler(object):

    def __init__(self, owner, **kwargs):
        for k in kwargs:
            setattr(self, k, kwargs[k])
        self.owner = owner

    def process_message(self, received):
        ignored = getattr(self, 'ignored_types', [])
        message_type = received.get('type', 'error')
        if not self.owner.authorized and message_type == 'error':
            message_type='auth'
        if message_type in ('new', 'change', 'delete', 'message') :
            res = self.process_notification(received)
        else:
            res = getattr(self, 'process_' + message_type)(received)
        if message_type in ignored:
            return
        return res

    def process_ping(self, received):
        self.owner.last_ping = received['timestamp']
        return received

    def process_auth(self, received):
        result = received['result']
        self.owner.authorized = result == 'OK'
        if self.owner.authorized:
            self.owner.uuid = received['uuid']
        return received

    def process_callresponse(self, received):
        if received['result'] == 'OK':
            return received
        return self.process_error(received['data'])

    @staticmethod
    def process_error(received):
        raise ApiException(received['error'])

    @staticmethod
    def process_notification(received):
        return received


class SyncanoClient(asyncore.dispatcher):

    def __init__(self, instance, api_key=None, login=None,
                 password=None, host=None, port=None, callback_handler=MessageHandler,
                 *args, **kwargs):

        asyncore.dispatcher.__init__(self)
        self.callback = callback_handler(self, *args, **kwargs) if callback_handler else None
        self.instance = instance
        self.login = login
        self.password = password
        self.api_key = api_key
        self.buffer = ''.encode('utf-8')
        self.results = []
        self.prepare_auth()
        self.create_socket(socket.AF_INET, socket.SOCK_STREAM)
        self.connect((host or HOST, port or PORT))
        self.authorized = None

    def write_to_buffer(self, data):
        data = json.dumps(data) + '\n'
        self.buffer = self.buffer + data.encode('utf-8')

    def clean_buffer(self, offset):
        self.buffer = self.buffer[offset:]

    def prepare_auth(self):
        auth = dict(instance=self.instance)
        if self.api_key:
            auth['api_key'] = self.api_key
        else:
            auth.update(dict(login=self.login, password=self.password))
        self.write_to_buffer(auth)

    def handle_connect(self):
        self.socket = ssl.wrap_socket(self.socket, do_handshake_on_connect=False)
        while True:
            try:
                self.socket.do_handshake()
            except ssl.SSLError as err:
                continue
            else:
                break

    def handle_close(self):
        self.close()

    def handle_read(self):
        received = self.recv(32768)
        received = received.decode('utf-8')
        received = json.loads(received)
        logger.info(u'received from server %s', received)
        if self.callback:
            res = self.callback.process_message(received)
            if res:
                self.results.append(res)
        else:
            self.results.append(received)

    def writable(self):
        return self.buffer

    def readable(self):
        return True

    def handle_write(self):
        logger.info(u'sent to server %s', self.buffer)
        sent = self.send(self.buffer)
        self.clean_buffer(sent)


class BaseMixin(object):

    @staticmethod
    def get_standard_params(method, message_id):
        attrs=dict(method=method, params=dict())
        if message_id:
            attrs['message_id'] = message_id
        return attrs

    def standard_method(self, method, message_id):
        attrs = self.get_standard_params(method, message_id)
        self.api_call(**attrs)

    def update_params(self, attrs, name, value):
        if value:
            attrs['params'][name] = value

    def method_has_result(self, name):
        if name.find('_delete') != -1:
            return False
        return True


class ClientMixin(BaseMixin):

    def client_heartbeat(self, message_id=None):
        params = dict(uuid=self.cli.uuid)
        atrs = dict(method='client.heartbeat', params=params)
        if message_id:
            atrs['message_id'] = message_id
        self.api_call(**atrs)

    def client_new(self, login, password, group_id=1,
                   first_name='', last_name='', email='', message_id=None):
        params = dict(client_login=login, client_password=password, group_id=group_id)
        if email:
            params['email'] = email
        if first_name:
            params['first_name'] = first_name
        if last_name:
            params['last_name'] = last_name
        atrs = dict(method='client.new', params=params)
        if message_id:
            atrs['message_id'] = message_id
        self.api_call(**atrs)

    def client_get(self, message_id=None):
        self.standard_method('client.get', message_id)

    def client_get_one(self, client_id=None, client_login=None, message_id=None):
        assert client_id or client_login, "client_id or client_login is required"
        attrs = self.get_standard_params('client.get_one', message_id)
        params = {}
        if client_id:
            params['client_id'] = client_id
        else:
            params['client_login'] = client_login
        attrs['params'] = params
        self.api_call(**attrs)

    def client_get_identities(self, client_id=None, client_login=None, name=None,
                              since_id=None, limit=100, message_id=None):
        attrs = self.get_standard_params('client.get_identities', message_id)
        params = dict(limit=limit)
        if client_id:
            params['client_id'] = client_id
        if client_login:
            params['client_login'] = client_login
        if name:
            params['name'] = name
        if since_id:
            params['since_id'] = since_id
        attrs['params'] = params
        self.api_call(**attrs)

    def client_get_groups(self, message_id=None):
        self.standard_method('client.get_groups', message_id)

    def client_update(self, client_id=None, client_login=None, new_login=None,
                      first_name=None, last_name=None, email=None, message_id=None):
        assert client_id or client_login, "client_id or client_login is required"
        attrs = self.get_standard_params('client.update', message_id)
        self.update_params(attrs, 'client_id', client_id)
        self.update_params(attrs, 'client_login', client_login)
        self.update_params(attrs, 'new_login', new_login)
        self.update_params(attrs, 'first_name', first_name)
        self.update_params(attrs, 'last_name', last_name)
        self.update_params(attrs, 'email', email)
        self.api_call(**attrs)

    def client_update_password(self, new_password, client_id=None, client_login=None,
                               current_password=None, message_id=None):
        assert client_id or client_login, "client_id or client_login is required"
        attrs = self.get_standard_params('client.update_password', message_id)
        attrs['params']['new_password'] = new_password
        self.update_params(attrs, 'client_id', client_id)
        self.update_params(attrs, 'client_login', client_login)
        self.update_params(attrs, 'current_password', current_password)
        self.api_call(**attrs)

    def client_update_state(self, state, client_id=None, client_login=None, uuid=None, message_id=None):
        attrs = self.get_standard_params('client.update_state', message_id)
        attrs['params']['state'] = state
        self.update_params(attrs, 'client_id', client_id)
        self.update_params(attrs, 'client_login', client_login)
        self.update_params(attrs, 'uuid', uuid)
        self.api_call(**attrs)

    def client_recreate_apikey(self, client_id=None, client_login=None, message_id=None):
        assert client_id or client_login, "client_id or client_login is required"
        attrs = self.get_standard_params('client.recreate_apikey', message_id)
        self.update_params(attrs, 'client_id', client_id)
        self.update_params(attrs, 'client_login', client_login)
        self.api_call(**attrs)

    def client_delete(self, client_id=None, client_login=None, message_id=None):
        assert client_id or client_login, "client_id or client_login is required"
        attrs = self.get_standard_params('client.delete', message_id)
        self.update_params(attrs, 'client_id', client_id)
        self.update_params(attrs, 'client_login', client_login)
        self.api_call(**attrs)


class ProjectMixin(BaseMixin):

    def project_new(self, name, message_id=None):
        attrs = self.get_standard_params('project.new', message_id)
        attrs['params']['name'] = name
        self.api_call(**attrs)

    def project_get(self, message_id=None):
        self.standard_method('project.get', message_id)

    def project_get_one(self, project_id, message_id=None):
        attrs = self.get_standard_params('project.get_one', message_id)
        attrs['params']['project_id'] = project_id
        self.api_call(**attrs)

    def project_update(self, project_id, name, message_id=None):
        attrs = self.get_standard_params('project.update', message_id)
        attrs['params']['name'] = name
        attrs['params']['project_id'] = project_id
        self.api_call(**attrs)

    def project_delete(self, project_id, message_id=None):
        attrs = self.get_standard_params('project.delete', message_id)
        attrs['params']['project_id'] = project_id
        self.api_call(**attrs)


class CollectionMixin(BaseMixin):

    def collection_new(self, project_id, name, key, message_id=None):
        attrs = self.get_standard_params('collection.new', message_id)
        attrs['params']['project_id'] = project_id
        attrs['params']['name'] = name
        attrs['params']['key'] = key
        self.api_call(**attrs)

    def collection_get(self,project_id, status='all', with_tags=None, message_id=None):
        attrs = self.get_standard_params('collection.get', message_id)
        attrs['params']['project_id'] = project_id
        self.update_params(attrs, 'status', status)
        self.update_params(attrs, 'with_tags', with_tags)
        self.api_call(**attrs)

    def collection_get_one(self, project_id, collection_id=None, collection_key=None, message_id=None):
        assert collection_id or collection_key, "collection_id or collection_key required"
        attrs = self.get_standard_params('collection.get_one', message_id)
        attrs['params']['project_id'] = project_id
        self.update_params(attrs, 'collection_id', collection_id)
        self.update_params(attrs, 'collection_key', collection_key)
        self.api_call(**attrs)

    def collection_activate(self, project_id, collection_id=None, collection_key=None, message_id=None):
        assert collection_id or collection_key, "collection_id or collection_key required"
        attrs = self.get_standard_params('collection.activate', message_id)
        attrs['params']['project_id'] = project_id
        self.update_params(attrs, 'collection_id', collection_id)
        self.update_params(attrs, 'collection_key', collection_key)
        self.api_call(**attrs)

    def collection_deactivate(self, project_id, collection_id=None, collection_key=None, message_id=None):
        assert collection_id or collection_key, "collection_id or collection_key required"
        attrs = self.get_standard_params('collection.deactivate', message_id)
        attrs['params']['project_id'] = project_id
        self.update_params(attrs, 'collection_id', collection_id)
        self.update_params(attrs, 'collection_key', collection_key)
        self.api_call(**attrs)

    def collection_update(self, project_id, collection_id, name=None, collection_key=None, message_id=None):
        attrs = self.get_standard_params('collection.update', message_id)
        attrs['params']['project_id'] = project_id
        attrs['params']['collection_id'] = collection_id
        self.update_params(attrs, 'name', name)
        self.update_params(attrs, 'collection_key', collection_key)
        self.api_call(**attrs)

    def collection_delete(self, project_id, collection_id=None, collection_key=None, message_id=None):
        assert collection_id or collection_key, "collection_id or collection_key required"
        attrs = self.get_standard_params('collection.delete', message_id)
        attrs['params']['project_id'] = project_id
        self.update_params(attrs, 'collection_id', collection_id)
        self.update_params(attrs, 'collection_key', collection_key)
        self.api_call(**attrs)

    def collection_add_tag(self, project_id, collection_id=None, collection_key=None,
                           tags=[], weight=1, remove_other=False, message_id=None):
        assert collection_id or collection_key, "collection_id or collection_key required"
        attrs = self.get_standard_params('collection.add_tag', message_id)
        attrs['params']['project_id'] = project_id
        self.update_params(attrs, 'collection_id', collection_id)
        self.update_params(attrs, 'collection_key', collection_key)
        self.update_params(attrs, 'tags', tags)
        self.update_params(attrs, 'weight', weight)
        self.update_params(attrs, 'remove_other', remove_other)
        self.api_call(**attrs)

    def collection_delete_tag(self, project_id, collection_id=None, collection_key=None, tags=[], message_id=None):
        assert collection_id or collection_key, "collection_id or collection_key required"
        attrs = self.get_standard_params('collection.delete_tag', message_id)
        attrs['params']['project_id'] = project_id
        self.update_params(attrs, 'collection_id', collection_id)
        self.update_params(attrs, 'collection_key', collection_key)
        self.update_params(attrs, 'tags', tags)
        self.api_call(**attrs)


class FolderMixin(BaseMixin):

    def folder_new(self, project_id, name, collection_id=None, collection_key=None, source_id=None, message_id=None):
        assert collection_id or collection_key, "collection_id or collection_key required"
        attrs = self.get_standard_params('folder.new', message_id)
        attrs['params']['project_id'] = project_id
        attrs['params']['name'] = name
        self.update_params(attrs, 'collection_id', collection_id)
        self.update_params(attrs, 'collection_key', collection_key)
        self.update_params(attrs, 'source_id', source_id)
        self.api_call(**attrs)

    def folder_get(self, project_id, collection_id=None, collection_key=None, message_id=None):
        assert collection_id or collection_key, "collection_id or collection_key required"
        attrs = self.get_standard_params('folder.get', message_id)
        attrs['params']['project_id'] = project_id
        self.update_params(attrs, 'collection_id', collection_id)
        self.update_params(attrs, 'collection_key', collection_key)
        self.api_call(**attrs)

    def folder_get_one(self, project_id, folder_name, collection_id=None, collection_key=None, message_id=None):
        assert collection_id or collection_key, "collection_id or collection_key required"
        attrs = self.get_standard_params('folder.get_one', message_id)
        attrs['params']['project_id'] = project_id
        attrs['params']['folder_name'] = folder_name
        self.update_params(attrs, 'collection_id', collection_id)
        self.update_params(attrs, 'collection_key', collection_key)
        self.api_call(**attrs)

    def folder_update(self, project_id, name, collection_id=None, collection_key=None,
                      new_name=None, source_id=None, message_id=None):
        assert collection_id or collection_key, "collection_id or collection_key required"
        attrs = self.get_standard_params('folder.update', message_id)
        attrs['params']['project_id'] = project_id
        attrs['params']['name'] = name
        self.update_params(attrs, 'collection_id', collection_id)
        self.update_params(attrs, 'collection_key', collection_key)
        self.update_params(attrs, 'new_name', new_name)
        self.update_params(attrs, 'source_id', source_id)
        self.api_call(**attrs)

    def folder_delete(self, project_id, name, collection_id=None, collection_key=None, message_id=None):
        assert collection_id or collection_key, "collection_id or collection_key required"
        attrs = self.get_standard_params('folder.delete', message_id)
        attrs['params']['project_id'] = project_id
        attrs['params']['name'] = name
        self.update_params(attrs, 'collection_id', collection_id)
        self.update_params(attrs, 'collection_key', collection_key)
        self.api_call(**attrs)


class DataObjectMixin(BaseMixin):

    def data_new(self, project_id, collection_id=None, collection_key=None,
                 user_name=None, source_url=None, title=None, text=None, link=None, image=None,
                 image_url=None, folder=None, state='Pending', data_key=None,
                 parent_id=None, message_id=None):
        assert collection_id or collection_key, "collection_id or collection_key required"
        attrs = self.get_standard_params('data.new', message_id)
        attrs['params']['project_id'] = project_id
        self.update_params(attrs, 'collection_id', collection_id)
        self.update_params(attrs, 'collection_key', collection_key)
        self.update_params(attrs, 'user_name', user_name)
        self.update_params(attrs, 'source_url', source_url)
        self.update_params(attrs, 'title', title)
        self.update_params(attrs, 'text', text)
        self.update_params(attrs, 'link', link)
        self.update_params(attrs, 'image', image)
        self.update_params(attrs, 'image_url', image_url)
        self.update_params(attrs, 'folder', folder)
        self.update_params(attrs, 'state', state)
        self.update_params(attrs, 'parent_id', parent_id)
        self.update_params(attrs, 'data_key', data_key)
        self.api_call(**attrs)

    def data_update(self, project_id, collection_id=None, collection_key=None, data_id=None, data_key=None,
                    update_method='replace', user_name=None, source_url=None, title=None, text=None,
                    link=None, image=None, image_url=None, folder=None, state=None, parent_id=None,
                    message_id=None):
        assert collection_id or collection_key, "collection_id or collection_key required"
        assert data_id or data_key, "data_id or data_key required"
        attrs = self.get_standard_params('data.update', message_id)
        attrs['params']['project_id'] = project_id
        self.update_params(attrs, 'collection_id', collection_id)
        self.update_params(attrs, 'collection_key', collection_key)
        self.update_params(attrs, 'data_id', data_id)
        self.update_params(attrs, 'data_key', data_key)
        self.update_params(attrs, 'update_method', update_method)
        self.update_params(attrs, 'user_name', user_name)
        self.update_params(attrs, 'source_url', source_url)
        self.update_params(attrs, 'title', title)
        self.update_params(attrs, 'text', text)
        self.update_params(attrs, 'link', link)
        self.update_params(attrs, 'image', image)
        self.update_params(attrs, 'image_url', image_url)
        self.update_params(attrs, 'folder', folder)
        self.update_params(attrs, 'state', state)
        self.update_params(attrs, 'parent_id', parent_id)
        self.api_call(**attrs)

    def data_get(self, project_id, collection_id=None, collection_key=None, state='All', folders=[], since_id=None,
                 max_id=None, since_time=None, limit=100, order='ASC', order_by='created_at', filter=None,
                 include_children=True, depth=None, children_limit=100, parent_ids=[], by_user=None, message_id=None):
        assert collection_id or collection_key, "collection_id or collection_key required"
        attrs = self.get_standard_params('data.get', message_id)
        attrs['params']['project_id'] = project_id
        self.update_params(attrs, 'collection_id', collection_id)
        self.update_params(attrs, 'collection_key', collection_key)
        self.update_params(attrs, 'state', state)
        self.update_params(attrs, 'folders', folders)
        self.update_params(attrs, 'since_id', since_id)
        self.update_params(attrs, 'max_id', max_id)
        self.update_params(attrs, 'since_time', since_time)
        self.update_params(attrs, 'limit', limit)
        self.update_params(attrs, 'order', order)
        self.update_params(attrs, 'order_by', order_by)
        self.update_params(attrs, 'filter', filter)
        self.update_params(attrs, 'include_children', include_children)
        self.update_params(attrs, 'depth', depth)
        self.update_params(attrs, 'children_limit', children_limit)
        self.update_params(attrs, 'parent_ids', parent_ids)
        self.update_params(attrs, 'by_user', by_user)
        self.api_call(**attrs)

    def data_get_one(self, project_id, collection_id=None, collection_key=None, data_id=None,
                     data_key=None, message_id=None):
        assert collection_id or collection_key, "collection_id or collection_key required"
        attrs = self.get_standard_params('data.get_one', message_id)
        attrs['params']['project_id'] = project_id
        self.update_params(attrs, 'collection_id', collection_id)
        self.update_params(attrs, 'collection_key', collection_key)
        self.update_params(attrs, 'data_id', data_id)
        self.update_params(attrs, 'data_key', data_key)
        self.api_call(**attrs)

    def data_move(self, project_id, collection_id=None, collection_key=None, data_ids=[], state='All', folders=[],
                  filter=None, by_user=None, limit=100, new_folder=None, new_state=None, message_id=None):
        assert collection_id or collection_key, "collection_id or collection_key required"
        attrs = self.get_standard_params('data.move', message_id)
        attrs['params']['project_id'] = project_id
        self.update_params(attrs, 'collection_id', collection_id)
        self.update_params(attrs, 'collection_key', collection_key)
        self.update_params(attrs, 'data_ids', data_ids)
        self.update_params(attrs, 'state', state)
        self.update_params(attrs, 'folders', folders)
        self.update_params(attrs, 'filter', filter)
        self.update_params(attrs, 'by_user', by_user)
        self.update_params(attrs, 'limit', limit)
        self.update_params(attrs, 'new_folder', new_folder)
        self.update_params(attrs, 'new_state', new_state)
        self.api_call(**attrs)

    def data_copy(self, project_id, data_ids, collection_id=None, collection_key=None, message_id=None):
        assert collection_id or collection_key, "collection_id or collection_key required"
        attrs = self.get_standard_params('data.copy', message_id)
        self.update_params(attrs, 'collection_id', collection_id)
        self.update_params(attrs, 'collection_key', collection_key)
        attrs['params']['project_id'] = project_id
        attrs['params']['data_ids'] = data_ids
        self.api_call(**attrs)

    def data_add_parent(self, project_id, data_id, collection_id=None, collection_key=None,
                        parent_id=None, remove_other=False, message_id=None):
        assert collection_id or collection_key, "collection_id or collection_key required"
        attrs = self.get_standard_params('data.add_parent', message_id)
        attrs['params']['project_id'] = project_id
        attrs['params']['data_id'] = data_id
        self.update_params(attrs, 'collection_id', collection_id)
        self.update_params(attrs, 'collection_key', collection_key)
        self.update_params(attrs, 'parent_id', parent_id)
        self.update_params(attrs, 'remove_other', remove_other)
        self.api_call(**attrs)

    def data_remove_parent(self, project_id, data_id, collection_id=None, collection_key=None,
                           parent_id=None, message_id=None):
        assert collection_id or collection_key, "collection_id or collection_key required"
        attrs = self.get_standard_params('data.remove_parent', message_id)
        attrs['params']['project_id'] = project_id
        attrs['params']['data_id'] = data_id
        self.update_params(attrs, 'collection_id', collection_id)
        self.update_params(attrs, 'collection_key', collection_key)
        self.update_params(attrs, 'parent_id', parent_id)
        self.api_call(**attrs)

    def data_delete(self, project_id, collection_id=None, collection_key=None, data_ids=[],
                    state='All', folders=None, filter=None, by_user=None, limit=100, message_id=None):
        assert collection_id or collection_key, "collection_id or collection_key required"
        attrs = self.get_standard_params('data.delete', message_id)
        attrs['params']['project_id'] = project_id
        self.update_params(attrs, 'collection_id', collection_id)
        self.update_params(attrs, 'collection_key', collection_key)
        self.update_params(attrs, 'data_ids', data_ids)
        self.update_params(attrs, 'state', state)
        self.update_params(attrs, 'folders', folders)
        self.update_params(attrs, 'filter', filter)
        self.update_params(attrs, 'by_user', by_user)
        self.update_params(attrs, 'limit', limit)
        self.api_call(**attrs)

    def data_count(self, project_id, collection_id=None, collection_key=None, state='All', folders=None,
                   filter=None, by_user=None, message_id=None):
        assert collection_id or collection_key, "collection_id or collection_key required"
        attrs = self.get_standard_params('data.count', message_id)
        attrs['params']['project_id'] = project_id
        self.update_params(attrs, 'collection_id', collection_id)
        self.update_params(attrs, 'collection_key', collection_key)
        self.update_params(attrs, 'state', state)
        self.update_params(attrs, 'folders', folders)
        self.update_params(attrs, 'filter', filter)
        self.update_params(attrs, 'by_user', by_user)
        self.api_call(**attrs)


class UserMixin(BaseMixin):

    def user_new(self, user_name, nick=None, avatar=None, message_id=None):
        attrs = self.get_standard_params('user.new', message_id)
        attrs['params']['user_name'] = user_name
        self.update_params(attrs, 'nick', nick)
        self.update_params(attrs, 'avatar', avatar)
        self.api_call(**attrs)

    def user_get_all(self, since_id=None, limit=100, message_id=None):
        attrs = self.get_standard_params('user.get_all', message_id)
        self.update_params(attrs, 'since_id', since_id)
        self.update_params(attrs, 'limit', limit)
        self.api_call(**attrs)

    def user_get(self, project_id, collection_id=None, collection_key=None,
                 state='All', folders=None, filter=None, message_id=None):
        assert collection_id or collection_key, "collection_id or collection_key required"
        attrs = self.get_standard_params('user.get', message_id)
        attrs['params']['project_id'] = project_id
        self.update_params(attrs, 'collection_id', collection_id)
        self.update_params(attrs, 'collection_key', collection_key)
        self.update_params(attrs, 'state', state)
        self.update_params(attrs, 'folders', folders)
        self.update_params(attrs, 'filter', filter)
        self.api_call(**attrs)

    def user_get_one(self, user_id=None, user_name=None, message_id=None):
        assert user_id or user_name, "user_id or user_name required"
        attrs = self.get_standard_params('user.get_one', message_id)
        self.update_params(attrs, 'user_id', user_id)
        self.update_params(attrs, 'user_name', user_name)
        self.api_call(**attrs)

    def user_update(self, user_id=None, user_name=None, nick=None, avatar=None, message_id=None):
        assert user_id or user_name, "user_id or user_name required"
        attrs = self.get_standard_params('user.update', message_id)
        self.update_params(attrs, 'user_id', user_id)
        self.update_params(attrs, 'user_name', user_name)
        self.update_params(attrs, 'nick', nick)
        self.update_params(attrs, 'avatar', avatar)
        self.api_call(**attrs)

    def user_count(self, project_id=None, collection_id=None, collection_key=None,
                   state='All', folders=None, filter=None, message_id=None):
        attrs = self.get_standard_params('user.count', message_id)
        self.update_params(attrs, 'project_id', project_id)
        self.update_params(attrs, 'collection_id', collection_id)
        self.update_params(attrs, 'collection_key', collection_key)
        self.update_params(attrs, 'state', state)
        self.update_params(attrs, 'folders', folders)
        self.update_params(attrs, 'filter', filter)
        self.api_call(**attrs)

    def user_delete(self, user_id=None, user_name=None, message_id=None):
        assert user_id or user_name, "user_id or user_name required"
        attrs = self.get_standard_params('user.delete', message_id)
        self.update_params(attrs, 'user_id', user_id)
        self.update_params(attrs, 'user_name', user_name)
        self.api_call(**attrs)


class NotificationMixin(BaseMixin):

    def notification_send(self, client_id=None, client_login=None, uuid=None, message_id=None, **kwargs):
        assert client_id or client_login, "client_id or client_login required"
        attrs = self.get_standard_params('notification.send', message_id)
        self.update_params(attrs, 'client_id', client_id)
        self.update_params(attrs, 'client_login', client_login)
        self.update_params(attrs, 'uuid', uuid)
        attrs['params'].update(kwargs)
        self.api_call(**attrs)

    def notification_get_history(self, client_id=None, client_login=None, since_id=None,
                                 since_time=None, limit=100, order=None, message_id=None):
        attrs = self.get_standard_params('notification.get_history', message_id)
        self.update_params(attrs, 'client_id', client_id)
        self.update_params(attrs, 'client_login', client_login)
        self.update_params(attrs, 'since_id', since_id)
        self.update_params(attrs, 'since_time', since_time)
        self.update_params(attrs, 'limit', limit)
        self.update_params(attrs, 'order', order)
        self.api_call(**attrs)

    def notification_get_collection_history(self, project_id, collection_id=None, collection_key=None,
                                            since_id=None, since_time=None, limit=100, order=None, message_id=None):
        assert collection_id or collection_key, "collection_id or collection_key required"
        attrs = self.get_standard_params('notification.get_collection_history', message_id)
        attrs['params']['project_id'] = project_id
        self.update_params(attrs, 'since_id', since_id)
        self.update_params(attrs, 'since_time', since_time)
        self.update_params(attrs, 'limit', limit)
        self.update_params(attrs, 'order', order)
        self.update_params(attrs, 'collection_id', collection_id)
        self.update_params(attrs, 'collection_key', collection_key)
        self.api_call(**attrs)


class SubscriptionMixin(BaseMixin):

    def subscription_subscribe_project(self, project_id, message_id=None):
        attrs = self.get_standard_params('subscription.subscribe_project', message_id)
        attrs['params']['project_id'] = project_id
        self.api_call(**attrs)

    def subscription_unsubscribe_project(self, project_id, message_id=None):
        attrs = self.get_standard_params('subscription.unsubscribe_project', message_id)
        attrs['params']['project_id'] = project_id
        self.api_call(**attrs)

    def subscription_subscribe_collection(self, project_id, collection_id=None, collection_key=None, message_id=None):
        assert collection_id or collection_key, "collection_id or collection_key required"
        attrs = self.get_standard_params('subscription.subscribe_collection', message_id)
        attrs['params']['project_id'] = project_id
        self.update_params(attrs, 'collection_id', collection_id)
        self.update_params(attrs, 'collection_key', collection_key)
        self.api_call(**attrs)

    def subscription_unsubscribe_collection(self, project_id, collection_id=None, collection_key=None, message_id=None):
        assert collection_id or collection_key, "collection_id or collection_key required"
        attrs = self.get_standard_params('subscription.unsubscribe_collection', message_id)
        attrs['params']['project_id'] = project_id
        self.update_params(attrs, 'collection_id', collection_id)
        self.update_params(attrs, 'collection_key', collection_key)
        self.api_call(**attrs)

    def subscription_get(self, client_id=None, client_login=None, message_id=None):
        attrs = self.get_standard_params('subscription.get', message_id)
        self.update_params(self, 'client_id', client_id)
        self.update_params(self, 'client_login', client_login)
        self.api_call(**attrs)


class SyncanoAsyncApi(ClientMixin, ProjectMixin, CollectionMixin, FolderMixin,
                      UserMixin, DataObjectMixin, NotificationMixin, SubscriptionMixin):

    def __init__(self, instance, api_key=None, login=None, password=None,
                 host=None, port=None, timeout=1, **kwargs):
        self.cli = SyncanoClient(instance, api_key=api_key, login=login,
                                 password=password, host=host, port=port, **kwargs)
        self.timeout = timeout
        self.cached_prefix = ''
        while self.cli.authorized is None:
            self.get_message(blocking=False)
            time.sleep(timeout)
        if not self.cli.authorized:
            raise AuthException

    def get_message(self, blocking=True, message_id=None):
        if message_id:
            for i,r in enumerate(self.cli.results):
                if r.get('message_id', None) == message_id:
                    return self.cli.results.pop(i)
        else:
            if self.cli.results:
                return self.cli.results.pop(0)
        while asyncore.socket_map:
            asyncore.loop(timeout=1, count=1)
            if message_id:
                for i, r in enumerate(self.cli.results):
                    if r.get('message_id', None) == message_id:
                        return self.cli.results.pop(i)
            else:
                if self.cli.results:
                    return self.cli.results.pop(0)
            if not blocking:
                return
        raise ConnectionLost

    def send_message(self, message):
        self.cli.write_to_buffer(message)

    def close(self):
        self.cli.handle_close()

    def __enter__(self):
        return self

    def __exit__(self, type, value, traceback):
        self.close()

    def __getattribute__(self, item):
        for prefix in ['client_', 'folder_', 'project_', 'collection_',
                       'data_', 'notification_', 'subscription_', 'user_']:
            if not item.startswith(prefix) and item.startswith(prefix[:-1]):
                self.cached_prefix = prefix
                return self
            elif item != 'cached_prefix':
                temp_prefix = self.cached_prefix
                self.cached_prefix = ''
                if temp_prefix:
                    return self.__getattribute__(temp_prefix + item)
        return super(SyncanoAsyncApi, self).__getattribute__(item)

    def api_call(self, **kwargs):
        data = {'type': 'call'}
        data.update(kwargs)
        self.send_message(data)


def api_result_decorator(f, instance):
    def wrapper(*args, **kwargs):
        message_id = kwargs.pop('message_id', int(time.time()*10**4))
        kwargs['message_id'] = message_id
        f(*args, **kwargs)
        if instance.method_has_result(f.__name__):
            return instance.get_message(blocking=True, message_id=message_id)
        else:
            for i in range(5):
                m = instance.get_message(blocking=False, message_id=message_id)
                if m:
                    break
            return True
    return wrapper


class SyncanoApi(SyncanoAsyncApi):

    def __getattribute__(self, item):
        for prefix in ['client_', 'folder_', 'project_', 'collection_',
                       'data_', 'notification_', 'subscription_', 'user_']:
            if item.startswith(prefix):
                return api_result_decorator(super(SyncanoApi, self).__getattribute__(item), self)
        return super(SyncanoApi, self).__getattribute__(item)