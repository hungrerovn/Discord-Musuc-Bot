import discord
from discord.ext import commands
import asyncio
import yt_dlp
import os
import datetime 

# --- Cấu hình Colors và Emoji ---
COLOR_SUCCESS = 0x20ff00  # Màu xanh lá cây #20ff00
COLOR_ERROR = 0xff0000    # Màu đỏ #ff0000

EMOJI_ERROR = "❌️"
EMOJI_PROCESSING = "🔄"
EMOJI_PLAYING = "🎶"
EMOJI_QUEUE = "📋"
EMOJI_PAUSE = "⏸️" 
EMOJI_WAVE = "👋" 
EMOJI_LEAVE = "🚪" 
EMOJI_SKIP = "⏭️" 
EMOJI_HEART = "❤" 

# Lấy đường dẫn tuyệt đối đến file cookie
COOKIE_FILE_PATH = os.path.join(os.getcwd(), 'cookies.txt')

# Cấu hình yt_dlp và FFmpeg
YTDL_OPTIONS = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'quiet': True,
    'extractaudio': True,
    'audioformat': 'mp3',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',
    'cookiefile': COOKIE_FILE_PATH,
}

FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn'
}

# Hàm tiện ích để định dạng thời lượng từ giây sang MM:SS hoặc HH:MM:SS
def format_duration(seconds):
    if seconds is None:
        return "N/A"
    minutes, seconds = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02}:{minutes:02}:{seconds:02}"
    return f"{minutes:02}:{seconds:02}"

# Custom check để kiểm tra quyền "music commander"
def is_music_commander():
    async def predicate(ctx):
        player = ctx.cog.get_player(ctx)
        # Nếu bot không trong kênh thoại hoặc chưa có người điều khiển, thì không ai có quyền dùng lệnh này
        if not player.voice_client or not player.voice_client.is_connected() or player._commander_id is None:
            return False # Sẽ gây ra commands.CheckFailure
        
        # Chỉ người dùng có ID khớp với _commander_id mới được phép
        return ctx.author.id == player._commander_id
    return commands.check(predicate)

# Lớp MusicPlayer để quản lý trạng thái nhạc cho từng guild
class MusicPlayer:
    def __init__(self, bot):
        self.bot = bot
        self.queue = asyncio.Queue()  
        self.current_track = None     
        self.voice_client = None      
        self._player_task = None      
        self._inactivity_timer = None 
        self._commander_id = None # ID của người điều khiển nhạc

    async def player_task(self):
        while True:
            try:
                # Đảm bảo bot đang phát hoặc tạm dừng trước khi lấy bài mới, hoặc là queue trống
                if not self.voice_client or (not self.voice_client.is_playing() and not self.voice_client.is_paused() and not self.queue.empty()):
                    self.cancel_inactivity_timer()
                
                # Luôn lấy bài hát tiếp theo khi không có bài nào đang phát
                if not self.voice_client or (not self.voice_client.is_playing() and not self.voice_client.is_paused()):
                    self.current_track = await self.queue.get() 

                # Xử lý khi bot mất kết nối (bị kick, network issue, ...)
                if not self.voice_client or not self.voice_client.is_connected():
                    print("Bot mất kết nối thoại, dừng player_task.")
                    
                    self.current_track = None
                    self.queue = asyncio.Queue() 
                    if self._player_task and not self._player_task.done():
                        pass 
                    self.cancel_inactivity_timer()
                    self._commander_id = None
                    self.voice_client = None 
                    break 

                ctx = self.current_track['ctx']
                url = self.current_track['url']
                title = self.current_track['title']
                original_url = self.current_track['original_url']
                
                # ĐIỀU CHỈNH: Gửi thông báo "Đang phát" tại đây cho MỌI bài hát
                embed = discord.Embed(
                    description=f"### {EMOJI_PLAYING}|đang Phát [{title}]({original_url})",
                    color=COLOR_SUCCESS
                )
                try:
                    # Nếu có response_msg từ lệnh play, chỉnh sửa nó
                    if 'response_msg' in self.current_track and self.current_track['response_msg']:
                        await self.current_track['response_msg'].edit(embed=embed)
                        # Sau khi chỉnh sửa, xóa nó đi để các bài sau gửi tin nhắn mới
                        self.current_track['response_msg'] = None 
                    else: # Nếu không có response_msg (bài tiếp theo trong queue), gửi tin nhắn mới
                        await ctx.send(embed=embed)
                except discord.HTTPException as e: # Log chi tiết lỗi HTTP
                    print(f"Lỗi HTTP khi gửi tin nhắn 'đang phát' cho bài '{title}' ở guild {ctx.guild.id}: {e}.")
                except Exception as e: # Log lỗi không xác định
                    print(f"Lỗi không xác định khi gửi tin nhắn 'đang phát' cho bài '{title}' ở guild {ctx.guild.id}: {e}.")
                
                source = discord.FFmpegPCMAudio(url, **FFMPEG_OPTIONS)
                self.voice_client.play(source, after=lambda e: asyncio.run_coroutine_threadsafe(self._after_playing(ctx, e), self.bot.loop).result())

                while self.voice_client.is_playing() or self.voice_client.is_paused():
                    await asyncio.sleep(1)

            except asyncio.CancelledError:
                print("player_task đã bị hủy.")
                # Nếu có response_msg còn sót lại khi hủy, chỉnh sửa nó
                if self.current_track and 'response_msg' in self.current_track and self.current_track['response_msg']:
                    try:
                        embed = discord.Embed(
                            description=f"### {EMOJI_ERROR}|Hoạt động phát nhạc đã bị hủy.",
                            color=COLOR_ERROR
                        )
                        await self.current_track['response_msg'].edit(embed=embed)
                    except discord.HTTPException:
                        pass
                self.current_track = None
                self.queue = asyncio.Queue() 
                self._commander_id = None 
                self.cancel_inactivity_timer()
                self.voice_client = None 
                break
            except Exception as e:
                print(f"Lỗi trong player_task: {e}")
                if self.voice_client and self.voice_client.is_playing():
                    self.voice_client.stop()
                
                if self.current_track and 'ctx' in self.current_track:
                    original_ctx = self.current_track['ctx']
                    embed = discord.Embed(
                        description=f"### {EMOJI_ERROR}|Đã xảy ra lỗi không mong muốn khi phát nhạc: {e}",
                        color=COLOR_ERROR
                    )
                    try:
                        await original_ctx.send(embed=embed)
                    except discord.HTTPException as e_http: # Log chi tiết lỗi HTTP
                        print(f"Lỗi HTTP khi gửi tin nhắn lỗi không mong muốn cho guild {original_ctx.guild.id}: {e_http}.")
                    except Exception as e_gen: # Log lỗi không xác định
                        print(f"Lỗi không xác định khi gửi tin nhắn lỗi không mong muốn cho guild {original_ctx.guild.id}: {e_gen}.")
                
                self.current_track = None 
                self.queue = asyncio.Queue() 
                self._commander_id = None 
                self.cancel_inactivity_timer() 
                self.voice_client = None 
                continue 

    async def _after_playing(self, ctx, error):
        """
        Callback được gọi sau khi một bài hát kết thúc.
        """
        if error:
            print(f'Lỗi khi phát: {error}')
            embed = discord.Embed(
                description=f"### {EMOJI_ERROR}|Đã xảy ra lỗi khi phát nhạc: {error}",
                color=COLOR_ERROR
            )
            try: # Bổ sung try-except cho lỗi khi gửi tin nhắn lỗi phát nhạc
                await ctx.send(embed=embed)
            except discord.HTTPException as e:
                print(f"Lỗi HTTP khi gửi tin nhắn lỗi phát nhạc cho guild {ctx.guild.id}: {e}.")
            except Exception as e:
                print(f"Lỗi không xác định khi gửi tin nhắn lỗi phát nhạc cho guild {ctx.guild.id}: {e}.")
        
        # Kiểm tra lại kết nối trước khi tiếp tục
        if self.voice_client and self.voice_client.is_connected():
            self.current_track = None
            self.queue.task_done()
            
            if self.queue.empty():
                if self.get_voice_channel_members() > 0:
                    embed_queue_empty = discord.Embed(
                        description=f"### {EMOJI_QUEUE}|Không Còn Bài Hát Nào Trong Danh sách Phát.", 
                        color=COLOR_SUCCESS
                    )
                    try:
                        await ctx.send(embed=embed_queue_empty)
                    except discord.HTTPException as e: # Log chi tiết lỗi HTTP
                        print(f"Lỗi HTTP khi gửi tin nhắn 'hàng đợi trống' cho guild {ctx.guild.id}: {e}.")
                    except Exception as e: # Log lỗi không xác định
                        print(f"Lỗi không xác định khi gửi tin nhắn 'hàng đợi trống' cho guild {ctx.guild.id}: {e}.")

                if self.get_voice_channel_members() == 0: 
                    print(f"Queue trống và không có ai trong kênh. Rời kênh {ctx.guild.id} ngay lập tức.")
                    await self.voice_client.disconnect()
                    self.voice_client = None
                    self.cancel_inactivity_timer() 
                    self._commander_id = None # Reset commander khi rời kênh do không hoạt động
                    embed_leave_immediately = discord.Embed(
                        description=f"### {EMOJI_QUEUE}|Không Còn Bài Hát Nào Trong Danh sách Phát. Mình Đi Đây.", 
                        color=COLOR_SUCCESS
                    )
                    try:
                        await ctx.send(embed=embed_leave_immediately)
                    except discord.HTTPException as e: # Log chi tiết lỗi HTTP
                        print(f"Lỗi HTTP khi gửi tin nhắn 'rời kênh ngay lập tức' cho guild {ctx.guild.id}: {e}.")
                    except Exception as e: # Log lỗi không xác định
                        print(f"Lỗi không xác định khi gửi tin nhắn 'rời kênh ngay lập tức' cho guild {ctx.guild.id}: {e}.")
                else: 
                    self.start_inactivity_timer(ctx)
        else: # Bot đã ngắt kết nối không mong muốn (hoặc bị đá/stop từ bên ngoài)
            self.current_track = None
            self.queue = asyncio.Queue() 
            if self._player_task and not self._player_task.done():
                self._player_task.cancel()
            self.cancel_inactivity_timer() 
            self._commander_id = None 
            self.voice_client = None 

    def get_voice_channel_members(self):
        """
        Kiểm tra số lượng thành viên (không phải bot) trong kênh thoại của bot.
        """
        if self.voice_client and self.voice_client.channel:
            members = [member for member in self.voice_client.channel.members if not member.bot]
            return len(members)
        return 0

    def start_inactivity_timer(self, ctx):
        """
        Bắt đầu hẹn giờ để bot rời kênh nếu không có ai trong voice channel.
        """
        self.cancel_inactivity_timer() 
        print(f"Bắt đầu hẹn giờ tự động rời kênh cho guild {ctx.guild.id}")
        self._inactivity_timer = self.bot.loop.create_task(self._inactivity_countdown(ctx))

    def cancel_inactivity_timer(self):
        """
        Hủy hẹn giờ tự động rời kênh.
        """
        if self._inactivity_timer and not self._inactivity_timer.done():
            self._inactivity_timer.cancel()
            print("Đã hủy hẹn giờ tự động rời kênh.")
        self._inactivity_timer = None

    async def _inactivity_countdown(self, ctx):
        """
        Đếm ngược thời gian chờ trước khi rời kênh.
        """
        await asyncio.sleep(60) # Chờ 60 giây (1 phút)
        if self.voice_client and self.voice_client.is_connected():
            if self.queue.empty() and self.get_voice_channel_members() == 0:
                print(f"Không có ai trong kênh thoại và queue trống sau 1 phút. Rời kênh {ctx.guild.id}")
                await self.voice_client.disconnect()
                self.voice_client = None
                self.cancel_inactivity_timer()
                self._commander_id = None 
                embed = discord.Embed(
                    description=f"### {EMOJI_WAVE}|Không Có Ai Nghe Nhạc Thì Mình Đi Đây.", 
                    color=COLOR_SUCCESS
                )
                try:
                    await ctx.send(embed=embed)
                except discord.HTTPException as e: # Log chi tiết lỗi HTTP
                    print(f"Lỗi HTTP khi gửi tin nhắn 'không có ai nghe nhạc' cho guild {ctx.guild.id}: {e}.")
                except Exception as e: # Log lỗi không xác định
                    print(f"Lỗi không xác định khi gửi tin nhắn 'không có ai nghe nhạc' cho guild {ctx.guild.id}: {e}.")
            else:
                self.cancel_inactivity_timer()


# Lớp Cog chứa các lệnh nhạc
class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.players = {}

    def get_player(self, ctx):
        """
        Lấy hoặc tạo MusicPlayer cho guild hiện tại.
        """
        if ctx.guild.id not in self.players:
            self.players[ctx.guild.id] = MusicPlayer(self.bot)
        return self.players[ctx.guild.id]

    @commands.Cog.listener()
    async def on_ready(self):
        print("Cog Music đã sẵn sàng!")

    # Xử lý lỗi khi người không phải music commander sử dụng lệnh bị hạn chế
    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        # Handle custom check failures
        if isinstance(error, commands.CheckFailure):
            embed = discord.Embed(
                description=f"### {EMOJI_ERROR}|Bạn không có quyền sử dụng lệnh này. Chỉ người bắt đầu phát nhạc mới có thể.",
                color=COLOR_ERROR
            )
            await ctx.send(embed=embed)
        # Nếu muốn xử lý các loại lỗi khác hoặc để chúng tự động lan truyền
        # else:
        #    raise error

    # Lệnh play (Công khai)
    @commands.command(name='play', aliases=['p'])
    async def play(self, ctx, *, search_term=None):
        player = self.get_player(ctx)
        response_msg = None 

        if not search_term:
            embed = discord.Embed(
                description=f"### {EMOJI_ERROR}|Vui Lòng Cung Cấp URL Hoặc Tên Bài Hát.",
                color=COLOR_ERROR
            )
            await ctx.send(embed=embed)
            return

        if not ctx.message.author.voice:
            embed = discord.Embed(
                description=f"### {EMOJI_ERROR}|Vui Lòng Vào Kênh Thoại Để Tiếp Tục.",
                color=COLOR_ERROR
            )
            await ctx.send(embed=embed)
            return

        if not player.voice_client: # Bot không ở trong kênh thoại, đây là lần đầu tiên kết nối
            channel = ctx.message.author.voice.channel
            player.voice_client = await channel.connect()
            
            if not player._player_task or player._player_task.done():
                player._player_task = self.bot.loop.create_task(player.player_task())
            
            player._commander_id = ctx.author.id
            print(f"Người dùng {ctx.author.name} (ID: {ctx.author.id}) đã trở thành music commander cho guild {ctx.guild.id}")

        elif ctx.author.voice and ctx.author.voice.channel != player.voice_client.channel:
            embed = discord.Embed(
                description=f"### {EMOJI_ERROR}|Bạn phải ở trong kênh thoại `{player.voice_client.channel.name}` để sử dụng lệnh này.",
                color=COLOR_ERROR
            )
            await ctx.send(embed=embed)
            return

        embed = discord.Embed(
            description=f"### {EMOJI_PROCESSING}|Đang Xử Lý. Vui Lòng Chờ...",
            color=COLOR_SUCCESS 
        )
        response_msg = await ctx.send(embed=embed)

        try:
            with yt_dlp.YoutubeDL(YTDL_OPTIONS) as ydl:
                info = ydl.extract_info(search_term, download=False)
                
                if 'entries' in info:
                    if not info['entries']:
                        embed = discord.Embed(
                            description=f"### {EMOJI_ERROR}|Không Tìm Thấy Bài Hát Bạn Mong Muốn.",
                            color=COLOR_ERROR
                        )
                        if response_msg: await response_msg.edit(embed=embed)
                        else: await ctx.send(embed=embed)
                        return
                    info = info['entries'][0]

                if not info:
                    embed = discord.Embed(
                        description=f"### {EMOJI_ERROR}|Không Tìm Thấy Bài Hát Bạn Mong Muốn.",
                        color=COLOR_ERROR
                    )
                    if response_msg: await response_msg.edit(embed=embed)
                    else: await ctx.send(embed=embed)
                    return

                url2 = info.get('url', info['formats'][0]['url'])
                title = info.get("title", "Không rõ")
                original_url = info.get("webpage_url", "https://youtube.com")
                duration = info.get("duration")

                track_data = {
                    'ctx': ctx, 
                    'url': url2, 
                    'title': title, 
                    'original_url': original_url,
                    'duration': duration, 
                    'response_msg': response_msg # Truyền response_msg để player_task chỉnh sửa
                }

                await player.queue.put(track_data)
                
                # Không gửi tin nhắn "Đã thêm vào danh sách phát" hay "Đang chuẩn bị"
                # nữa mà để player_task lo liệu
                # (Lệnh play chỉ gửi "Đang xử lý..." ban đầu và response_msg sẽ được sửa bởi player_task)
                    
        except Exception as e:
            print(f"Lỗi YTDL: {e}")
            embed = discord.Embed(
                description=f"### {EMOJI_ERROR}|Đã xảy ra lỗi khi tìm kiếm hoặc thêm nhạc: {e}",
                color=COLOR_ERROR
            )
            if response_msg: await response_msg.edit(embed=embed)
            else: await ctx.send(embed=embed)

    # Lệnh queue/playlist (Công khai)
    @commands.command(name='queue', aliases=['playlist']) 
    async def queue_command(self, ctx):
        """
        Hiển thị bài hát đang phát và danh sách các bài hát trong hàng đợi.
        """
        player = self.get_player(ctx)

        if not player.voice_client or not player.voice_client.is_connected():
            embed = discord.Embed(
                description=f"### {EMOJI_ERROR}|Bot không ở trong kênh thoại nào cả.",
                color=COLOR_ERROR
            )
            await ctx.send(embed=embed)
            return

        if not player.current_track and player.queue.empty():
            embed = discord.Embed(
                title=f"{EMOJI_QUEUE}**|Danh Sách Phát!**",
                description=f"Không có bài hát.", 
                color=COLOR_ERROR
            )
            await ctx.send(embed=embed)
            return

        description = ""
        if player.current_track:
            status_emoji = EMOJI_PAUSE if player.voice_client.is_paused() else EMOJI_PLAYING
            current_duration_str = format_duration(player.current_track['duration']) if player.current_track['duration'] else "N/A"

            description += f"### {status_emoji}|Đang Phát:\n"
            description += f"`{current_duration_str}` | [{player.current_track['title']}]({player.current_track['original_url']})\n"
        
        if not player.queue.empty():
            description += f"### {EMOJI_QUEUE}|Danh sách Phát:\n"
            queued_songs = list(player.queue._queue) 
            
            for i, track in enumerate(queued_songs):
                duration_str = format_duration(track['duration'])
                description += f"`{duration_str}` | [{track['title']}]({track['original_url']})\n"
                if i >= 9: 
                    description += f"... và {player.queue.qsize() - (i + 1)} bài khác.\n"
                    break

        embed = discord.Embed(
            title=f"{EMOJI_QUEUE}**|Danh Sách Phát!**",
            description=description,
            color=COLOR_SUCCESS
        )
        await ctx.send(embed=embed)

    # Lệnh skip (Chỉ Music Commander)
    @commands.command(name='skip', aliases=['s'])
    @is_music_commander() 
    async def skip_command(self, ctx):
        """
        Bỏ qua bài hát hiện tại. (Chỉ người bắt đầu phát nhạc)
        """
        player = self.get_player(ctx)

        if not player.voice_client or not player.voice_client.is_connected():
            embed = discord.Embed(
                description=f"### {EMOJI_ERROR}|Bot không ở trong kênh thoại nào cả.",
                color=COLOR_ERROR
            )
            await ctx.send(embed=embed)
            return

        if not player.voice_client.is_playing() and not player.voice_client.is_paused():
            embed = discord.Embed(
                description=f"### {EMOJI_ERROR}|Bot hiện không phát nhạc.",
                color=COLOR_ERROR
            )
            await ctx.send(embed=embed)
            return
        
        if ctx.author.voice and ctx.author.voice.channel != player.voice_client.channel:
            embed = discord.Embed(
                description=f"### {EMOJI_ERROR}|Bạn phải ở trong kênh thoại `{player.voice_client.channel.name}` để bỏ qua bài hát.",
                color=COLOR_ERROR
            )
            await ctx.send(embed=embed)
            return

        skipped_track_title = "Không rõ"
        skipped_track_url = "#" 
        if player.current_track:
            skipped_track_title = player.current_track['title']
            skipped_track_url = player.current_track['original_url']

        player.voice_client.stop()

        embed = discord.Embed(
            description=f"### {EMOJI_SKIP}|[{skipped_track_title}]({skipped_track_url}) Đã Được Bỏ Qua.",
            color=COLOR_SUCCESS
        )
        await ctx.send(embed=embed)

    # Lệnh stop (Chỉ Music Commander)
    @commands.command(name='stop')
    @is_music_commander() 
    async def stop_command(self, ctx):
        """
        Dừng phát nhạc và ngắt kết nối bot khỏi kênh thoại. (Chỉ người bắt đầu phát nhạc)
        """
        player = self.get_player(ctx)

        if not player.voice_client or not player.voice_client.is_connected():
            embed = discord.Embed(
                description=f"### {EMOJI_ERROR}|Bot không ở trong kênh thoại nào cả.",
                color=COLOR_ERROR
            )
            await ctx.send(embed=embed)
            return

        if ctx.author.voice and ctx.author.voice.channel != player.voice_client.channel:
            embed = discord.Embed(
                description=f"### {EMOJI_ERROR}|Bạn phải ở trong kênh thoại `{player.voice_client.channel.name}` để dừng nhạc.",
                color=COLOR_ERROR
            )
            await ctx.send(embed=embed)
            return
        
        if player.voice_client.is_playing() or player.voice_client.is_paused():
            player.voice_client.stop()
        
        player.queue = asyncio.Queue()
        player.current_track = None

        if player._player_task and not player._player_task.done():
            player._player_task.cancel()
        player.cancel_inactivity_timer()

        await player.voice_client.disconnect()
        player.voice_client = None 
        player._commander_id = None # Reset commander sau khi dừng

        embed = discord.Embed(
            description=f"### {EMOJI_HEART}|Cảm ơn Bạn Đã Nghe Nhạc Cùng Bot",
            color=COLOR_SUCCESS
        )
        await ctx.send(embed=embed)


# Hàm setup BẮT BUỘC để bot có thể tải Cog này
async def setup(bot):
    await bot.add_cog(Music(bot))
