import discord
from discord.ext import commands
import os
from dotenv import load_dotenv

# Tải biến môi trường từ file .env
load_dotenv() 
TOKEN = os.getenv('DISCORD_TOKEN')

# Cấu hình intents (quyền của bot)
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True

# Khởi tạo Bot với command prefix và intents
bot = commands.Bot(command_prefix='h!', intents=intents, help_command=None) 

@bot.event
async def on_ready():
    print(f'Bot đã đăng nhập với tên: {bot.user.name}')
    print(f'ID của Bot: {bot.user.id}')
    print('Đang tải các Cogs...')

    for filename in os.listdir('./cogs'):
        if filename.endswith('.py') and not filename.startswith('__'): 
            try:
                await bot.load_extension(f'cogs.{filename[:-3]}')
                print(f'  Đã tải Cog: {filename[:-3]}')
            except Exception as e:
                print(f'  Không thể tải Cog: {filename[:-3]}. Lỗi: {e}')
    
    print('Tất cả Cogs đã được tải.')
    print('--------------------')
    print('Bot đã sẵn sàng hoạt động!')

# Chạy bot
if TOKEN:
    print("Đang khởi động bot...")
    bot.run(TOKEN)
else:
    print("LỖI: Không tìm thấy DISCORD_TOKEN trong file .env.")

