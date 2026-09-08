# server.py
import asyncio
import json
import uuid
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class TetrisServer:
    def __init__(self):
        self.rooms = {}  # room_id -> {'host': client_id, 'game_mode': str, 'players': {client_id: player_info}, 'state': {}}
        self.clients = {}  # client_id -> (reader, writer)

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

    async def process_message(self, client_id, msg):
        action = msg.get('action')
        if action == 'create_room':
            room_id = str(uuid.uuid4())
            game_mode = msg.get('game_mode', 'vs')
            self.rooms[room_id] = {
                'host': client_id,
                'game_mode': game_mode, # Сохраняем режим комнаты
                'players': {client_id: {**msg.get('player_info', {}), 'is_spectator': False}},
                'state': {}
            }
            await self.send_to(client_id, {'action': 'room_created', 'room_id': room_id, 'is_host': True, 'is_spectator': False})
            logging.info(f"[*] Комната создана: {room_id}, режим: {game_mode}, хост: {client_id}")

        elif action == 'join_room':
            room_id = msg.get('room_id')
            if room_id in self.rooms:
                room = self.rooms[room_id]
                is_spectator = False

                # ПРОВЕРКА ЛИМИТА ДЛЯ CO-OP (ОНЛАЙН)
                if room.get('game_mode') == 'coop' and len(room['players']) >= 8:
                    is_spectator = True
                    logging.info(f"[*] Игрок {client_id} присоединился к CO-OP комнате {room_id} как НАБЛЮДАТЕЛЬ (лимит 8)")
                elif len(room['players']) >= 17: # Общий технический лимит для других режимов
                    await self.send_to(client_id, {'action': 'error', 'message': 'Комната переполнена (макс. 17 игроков).'})
                    return

                room['players'][client_id] = {**msg.get('player_info', {}), 'is_spectator': is_spectator}
                is_host = (client_id == room['host'])

                await self.send_to(client_id, {
                    'action': 'room_joined',
                    'room_id': room_id,
                    'is_host': is_host,
                    'is_spectator': is_spectator,
                    'players': room['players']
                })
                await self.broadcast_room(room_id, {'action': 'player_joined', 'players': room['players']})
                logging.info(f"[*] Игрок {client_id} присоединился к комнате {room_id} (spectator={is_spectator})")
            else:
                await self.send_to(client_id, {'action': 'error', 'message': 'Комната не найдена'})

        elif action == 'update_state':
            room_id = msg.get('room_id')
            # Наблюдатели не должны отправлять состояние, но на всякий случай фильтруем
            if room_id in self.rooms and not self.rooms[room_id]['players'].get(client_id, {}).get('is_spectator'):
                self.rooms[room_id]['state'][client_id] = msg.get('state')
                await self.broadcast_room(room_id, {'action': 'state_update', 'state': self.rooms[room_id]['state']})

        elif action == 'leave_room':
            room_id = msg.get('room_id')
            if room_id in self.rooms:
                await self.remove_player_from_room(client_id, room_id)

    async def broadcast_room(self, room_id, msg):
        if room_id in self.rooms:
            for cid in self.rooms[room_id]['players']:
                await self.send_to(cid, msg)

    async def send_to(self, client_id, msg):
        if client_id in self.clients:
            reader, writer = self.clients[client_id]
            try:
                writer.write((json.dumps(msg) + '\n').encode('utf-8'))
                await writer.drain()
            except Exception:
                await self.remove_client(client_id)

    async def remove_player_from_room(self, client_id, room_id):
        if room_id in self.rooms:
            room = self.rooms[room_id]
            if client_id in room['players']:
                del room['players'][client_id]
                if not room['players']:
                    del self.rooms[room_id]
                    logging.info(f"[*] Комната {room_id} удалена (пустая)")
                else:
                    if room['host'] == client_id:
                        room['host'] = list(room['players'].keys())[0]
                        logging.info(f"[*] Новый хост комнаты {room_id}: {room['host']}")
                    await self.broadcast_room(room_id, {'action': 'player_left', 'players': room['players']})

    async def remove_client(self, client_id):
        if client_id in self.clients:
            del self.clients[client_id]
        for room_id in list(self.rooms.keys()):
            if client_id in self.rooms[room_id]['players']:
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
