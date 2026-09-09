import asyncio
import json
import uuid
import logging
import hashlib

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)


class TetrisServer:
    def __init__(self):
        self.rooms = {}
        self.clients = {}

    async def handle_client(self, reader, writer):
        client_id = str(uuid.uuid4())
        self.clients[client_id] = (reader, writer)

        logging.info(f"[+] Клиент подключился: {client_id}")

        try:
            while True:
                data = await reader.readline()

                if not data:
                    break

                msg = json.loads(data.decode('utf-8').strip())
                await self.process_message(client_id, msg)

        except Exception as e:
            logging.info(f"[-] Клиент отключился: {client_id}, ошибка: {e}")

        finally:
            await self.remove_client(client_id)
            writer.close()
            await writer.wait_closed()

    def _make_state_hash(self, state):
        try:
            dumped = json.dumps(state, sort_keys=True)
            return hashlib.md5(dumped.encode()).hexdigest()[:8]
        except Exception:
            return None

    async def process_message(self, client_id, msg):
        action = msg.get('action')
        if action == 'create_room':
            # ✅ ИСПРАВЛЕНО: используем имя комнаты, заданное клиентом
            room_id = msg.get('room_id') or str(uuid.uuid4())
            game_mode = msg.get('game_mode', 'vs')
            player_info = msg.get('player_info', {})

            # Если комната с таким именем уже есть — не пересоздаём
            if room_id in self.rooms:
                await self.send_to(client_id, {
                    'action': 'error',
                    'message': f'Комната "{room_id}" уже существует'
                })
                return

            self.rooms[room_id] = {
                'host': client_id,
                'game_mode': game_mode,
                'players': {},
                'state': {},
                'last_state_hash': None
            }

            your_player_ids = self._register_players(room_id, client_id, player_info)

            await self.send_to(client_id, {
                'action': 'room_created',
                'room_id': room_id,
                'is_host': True,
                'is_spectator': False,
                'your_player_ids': your_player_ids,
                'players': self.rooms[room_id]['players']
            })

            logging.info(
                f"[*] Комната создана: {room_id}, режим: {game_mode}, "
                f"хост: {client_id}, игроков: {len(your_player_ids)}"
            )

        elif action == 'join_room':
            room_id = msg.get('room_id')

            if room_id not in self.rooms:
                await self.send_to(client_id, {
                    'action': 'error',
                    'message': 'Комната не найдена'
                })
                return

            room = self.rooms[room_id]
            player_info = msg.get('player_info', {})

            if len(room['players']) + len(player_info) > 17:
                await self.send_to(client_id, {
                    'action': 'error',
                    'message': 'Комната переполнена (макс. 17 игроков).'
                })
                return

            your_player_ids = self._register_players(room_id, client_id, player_info)

            await self.send_to(client_id, {
                'action': 'room_joined',
                'room_id': room_id,
                'is_host': False,
                'is_spectator': False,
                'your_player_ids': your_player_ids,
                'players': room['players']
            })

            await self.broadcast_room(room_id, {
                'action': 'player_joined',
                'players': room['players']
            })

            logging.info(
                f"[*] Клиент {client_id} присоединился к комнате {room_id}, "
                f"добавлено игроков: {len(your_player_ids)}"
            )

        elif action == 'update_state':
            room_id = msg.get('room_id')

            if room_id not in self.rooms:
                return

            room = self.rooms[room_id]
            state = msg.get('state', {})

            if not state:
                return

            changed = False

            for local_id, player_state in state.items():
                global_id = f"{client_id}:{local_id}"

                if global_id not in room['players']:
                    continue

                old_state = room['state'].get(global_id)

                if old_state != player_state:
                    room['state'][global_id] = player_state
                    changed = True

            if changed:
                state_hash = self._make_state_hash(room['state'])

                if state_hash != room.get('last_state_hash'):
                    logging.info(
                        f"[~] Комната {room_id}: состояние изменилось, "
                        f"игроков в состоянии: {len(state)}, hash={state_hash}"
                    )
                    room['last_state_hash'] = state_hash

                await self.broadcast_states(room_id)

        elif action == 'leave_room':
            room_id = msg.get('room_id')

            if room_id in self.rooms:
                await self.remove_player_from_room(client_id, room_id)

    def _register_players(self, room_id, client_id, player_info):
        your_player_ids = {}

        room = self.rooms[room_id]

        for local_id, info in player_info.items():
            global_id = f"{client_id}:{local_id}"

            room['players'][global_id] = {
                'client_id': client_id,
                'local_id': local_id,
                'nickname': info.get('nickname', f'Player {local_id}'),
                'color': info.get('color', '#FFFFFF'),
                'is_spectator': info.get('is_spectator', False)
            }

            your_player_ids[str(local_id)] = global_id

        return your_player_ids

    async def broadcast_states(self, room_id):
        if room_id not in self.rooms:
            return

        room = self.rooms[room_id]

        client_ids = {
            data.get('client_id')
            for data in room['players'].values()
            if data.get('client_id')
        }

        for target_client_id in client_ids:
            prefix = f"{target_client_id}:"

            state_for_client = {
                global_id: player_state
                for global_id, player_state in room['state'].items()
                if not global_id.startswith(prefix)
            }

            await self.send_to(target_client_id, {
                'action': 'state_update',
                'state': state_for_client
            })

    async def broadcast_room(self, room_id, msg):
        if room_id not in self.rooms:
            return

        client_ids = {
            data.get('client_id')
            for data in self.rooms[room_id]['players'].values()
            if data.get('client_id')
        }

        for target_client_id in client_ids:
            await self.send_to(target_client_id, msg)

    async def send_to(self, client_id, msg):
        if client_id not in self.clients:
            return

        reader, writer = self.clients[client_id]

        try:
            writer.write((json.dumps(msg) + '\n').encode('utf-8'))
            await writer.drain()

        except Exception:
            await self.remove_client(client_id)

    async def remove_player_from_room(self, client_id, room_id):
        if room_id not in self.rooms:
            return

        room = self.rooms[room_id]

        removed = []

        for global_id in list(room['players'].keys()):
            if room['players'][global_id].get('client_id') == client_id:
                del room['players'][global_id]
                removed.append(global_id)

        for global_id in removed:
            if global_id in room['state']:
                del room['state'][global_id]

        if removed:
            logging.info(
                f"[-] Клиент {client_id} покинул комнату {room_id}, "
                f"удалено игроков: {len(removed)}"
            )

        if not room['players']:
            del self.rooms[room_id]
            logging.info(f"[*] Комната {room_id} удалена (пустая)")
            return

        client_ids = {
            data.get('client_id')
            for data in room['players'].values()
            if data.get('client_id')
        }

        if room['host'] == client_id and client_ids:
            room['host'] = list(client_ids)[0]
            logging.info(f"[*] Новый хост комнаты {room_id}: {room['host']}")

        await self.broadcast_room(room_id, {
            'action': 'player_left',
            'players': room['players']
        })

    async def remove_client(self, client_id):
        if client_id in self.clients:
            del self.clients[client_id]

        for room_id in list(self.rooms.keys()):
            has_client = any(
                data.get('client_id') == client_id
                for data in self.rooms[room_id]['players'].values()
            )

            if has_client:
                await self.remove_player_from_room(client_id, room_id)


async def main():
    server = TetrisServer()

    host = '0.0.0.0'
    port = 8888

    logging.info(f"[*] Глобальный сервер запущен на {host}:{port}")

    srv = await asyncio.start_server(server.handle_client, host, port)

    async with srv:
        await srv.serve_forever()


if __name__ == '__main__':
    try:
        asyncio.run(main())

    except KeyboardInterrupt:
        logging.info("Сервер остановлен.")
