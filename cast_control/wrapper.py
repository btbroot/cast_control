from __future__ import annotations
from typing import Optional, Any, List, Union, Tuple, \
  NamedTuple, Callable, Set

try:
  from typing import Protocol

except ImportError:
  from typing_extensions import Protocol

from pathlib import Path
from mimetypes import guess_type
from functools import lru_cache

from pychromecast.controllers.receiver import CastStatus
from pychromecast.controllers.media import MediaStatus, \
  BaseController, MediaController
from pychromecast.controllers.youtube import YouTubeController
from pychromecast.controllers.spotify import SpotifyController
from pychromecast.controllers.dashcast import DashCastController
# from pychromecast.controllers.homeassistant import HomeAssistantController
# from pychromecast.controllers.plex import PlexApiController, PlexController
from pychromecast import Chromecast

from mpris_server.adapters import Metadata, PlayState, \
  Microseconds, VolumeDecimal, RateDecimal
from mpris_server.base import BEGINNING, DEFAULT_RATE, DbusObj, \
  Track, Album, Artist
from mpris_server.compat import get_dbus_name, enforce_dbus_length

from .base import DEFAULT_THUMB, LIGHT_THUMB, NO_DURATION, NO_DELTA, \
  US_IN_SEC, DEFAULT_DISC_NO, MediaType, NO_DESKTOP_FILE, \
  NAME, create_desktop_file


DEFAULT_NAME: str = NAME
NO_ARTIST: str = ''
TITLE_SEP: str = ' - '
MAX_TITLES: int = 3

YOUTUBE_URLS: Set[str] = {
  'youtube.com/',
  'youtu.be/'
}
YT_LONG, YT_SHORT = YOUTUBE_URLS
YT_VID_URL: str = f'https://{YT_LONG}watch?v='

RESOLUTION: int = 1
NO_SUFFIX: str = ''
# NO_DURATION = None


class Titles(NamedTuple):
  title: Optional[str] = None
  artist: Optional[str] = None
  album: Optional[str] = None


class Wrapper(Protocol):
  dev: Chromecast
  light_icon: bool = False

  @property
  def cast_status(self) -> Optional[CastStatus]:
    pass

  @property
  def media_status(self) -> Optional[MediaStatus]:
    pass

  @property
  def media_controller(self) -> MediaController:
    pass

  @property
  def titles(self) -> Titles:
    pass


class StatusMixin(Wrapper):
  def __getattr__(self, name: str) -> Any:
    return getattr(self.dev, name)

  @property
  def cast_status(self) -> Optional[CastStatus]:
    if self.dev.status:
      return self.dev.status

    return None

  @property
  def media_status(self) -> Optional[MediaStatus]:
    if self.media_controller.status:
      return self.media_controller.status

    return None

  @property
  def media_controller(self) -> MediaController:
    return self.dev.media_controller


class ControllersMixin(Wrapper):
  def __init__(self):
    self.yt_ctl, self.spotify_ctl, self.dash_ctl = ctls = [
      YouTubeController(),
      SpotifyController(),
      DashCastController(),
    ]

    for ctl in ctls:
      self._register(ctl)

    super().__init__()

  def _register(self, controller: BaseController):
    self.dev.register_handler(controller)

  def _launch_youtube(self):
    self.yt_ctl.launch()

  def _play_youtube(self, video_id: str):
    if not self.yt_ctl.is_active:
      self._launch_youtube()

    self.yt_ctl.play_video(video_id)

  def _get_url(self) -> Optional[str]:
    content_id = None

    if self.media_status:
      content_id = self.media_status.content_id

    if content_id and 'http' not in content_id and self.yt_ctl.is_active:
      return f'{YT_VID_URL}{content_id}'

    return content_id

  def open_uri(self, uri: str):
    video_id = get_video_id(uri)

    if video_id:
      self._play_youtube(video_id)
      return

    mimetype, _ = guess_type(uri)
    self.media_controller.play_media(uri, mimetype)

  def add_track(
    self,
    uri: str,
    after_track: DbusObj,
    set_as_current: bool
  ):
    video_id = get_video_id(uri)

    if video_id:
      self.yt_ctl.add_to_queue(video_id)

    if video_id and set_as_current:
      self.yt_ctl.play_video(video_id)

    elif set_as_current:
      self.open_uri(uri)


class TitlesMixin(Wrapper):
  @property
  def titles(self) -> Titles:
    titles: List[str] = list()

    title = self.media_controller.title

    if title:
      titles.append(title)

    subtitle = self.get_subtitle()

    if subtitle:
      titles.append(subtitle)

    if self.media_status:
      artist = self.media_status.artist

      if artist:
        titles.append(artist)

      album = self.media_status.album_name

      if album:
        titles.append(album)

    app_name = self.dev.app_display_name

    if app_name:
      titles.append(app_name)

    titles = titles[:MAX_TITLES]

    return Titles(*titles)

  def get_subtitle(self) -> Optional[str]:
    if not self.media_status:
      return None

    metadata = self.media_status.media_metadata

    if metadata and 'subtitle' in metadata:
      return metadata['subtitle']

    return None


class TimeMixin(Wrapper):
  def __init__(self,):
    self._longest_duration: float = NO_DURATION
    super().__init__()

  def get_duration(self) -> Microseconds:
    duration: Optional[float] = None

    if self.media_status:
      duration = self.media_status.duration

    current = self.get_current_position()
    longest = self._longest_duration

    if duration:
      return duration * US_IN_SEC

    elif longest and longest > current:
      return longest

    elif current:
      self._longest_duration = current
      return current

    return NO_DURATION

  def get_current_position(self) -> Microseconds:
    status = self.media_status

    if not status:
      return BEGINNING

    position_secs = status.adjusted_current_time

    if position_secs:
      return int(position_secs * US_IN_SEC)

    return BEGINNING

  def on_new_status(self, *args, **kwargs):
    # super().on_new_status(*args, **kwargs)
    if not self.has_current_time():
      self._longest_duration = None

  def has_current_time(self) -> bool:
    status = self.media_status

    if not status or not status.current_time:
      return False

    current_time = round(status.current_time, RESOLUTION)

    return current_time > BEGINNING

  def seek(self, time: Microseconds):
    seconds = int(round(time / US_IN_SEC))
    self.media_controller.seek(seconds)

  def get_rate(self) -> RateDecimal:
    if not self.media_status:
      return DEFAULT_RATE

    rate = self.media_status.playback_rate

    if rate:
      return rate

    return DEFAULT_RATE

  def set_rate(self, val: RateDecimal):
    pass


class IconsMixin(Wrapper):
  def set_icon(self, lighter: bool = False):
    self.light_icon: bool = lighter

  def get_art_url(self, track: Optional[int] = None) -> str:
    thumb = self.media_controller.thumbnail

    if thumb:
      return thumb

    icon: Optional[str] = None

    if self.cast_status:
      icon = self.cast_status.icon_url

    if icon:
      return icon

    if self.light_icon:
      return str(LIGHT_THUMB)

    return str(DEFAULT_THUMB)

  @lru_cache
  def get_desktop_entry(self) -> str:
    path = create_desktop_file(light_icon=self.light_icon)

    if not path:
      return NO_DESKTOP_FILE

    # mpris requires stripped suffix
    path = path.with_suffix(NO_SUFFIX)

    return str(path)


class MetadataMixin(Wrapper):
  def metadata(self) -> Metadata:
    title, artist, album = self.titles

    artists = [artist] if artist else []
    dbus_name: DbusObj = get_track_id(title)
    comments: List[str] = []
    track_no: Optional[int] = None

    if self.media_status:
      track_no = self.media_status.track

    metadata = {
      'mpris:trackid': dbus_name,
      'mpris:length': self.get_duration(),
      'mpris:artUrl': self.get_art_url(),
      'xesam:url': self._get_url(),
      'xesam:title': title,
      'xesam:artist': artists,
      'xesam:album': album,
      'xesam:albumArtist': artists,
      'xesam:discNumber': DEFAULT_DISC_NO,
      'xesam:trackNumber': track_no,
      'xesam:comment': comments,
    }

    return metadata


class PlaybackMixin(Wrapper):
  def get_playstate(self) -> PlayState:
    if self.media_controller.is_paused:
      return PlayState.PAUSED

    elif self.media_controller.is_playing:
      return PlayState.PLAYING

    return PlayState.STOPPED

  def is_repeating(self) -> bool:
    return False

  def is_playlist(self) -> bool:
    return self.can_go_next() or self.can_go_previous()

  def get_shuffle(self) -> bool:
    return False

  def set_shuffle(self, val: bool):
    return False

  def play_next(self):
    self.media_controller.queue_next()

  def play_prev(self):
    self.media_controller.queue_prev()

  def quit(self):
    self.dev.quit_app()

  def next(self):
    self.play_next()

  def previous(self):
    self.play_prev()

  def pause(self):
    self.media_controller.pause()

  def resume(self):
    self.play()

  def stop(self):
    self.media_controller.stop()

  def play(self):
    self.media_controller.play()

  def set_repeating(self, val: bool):
    pass

  def set_loop_status(self, val: str):
    pass


class VolumeMixin(Wrapper):
  def get_volume(self) -> VolumeDecimal:
    if not self.cast_status:
      return None

    return self.cast_status.volume_level

  def set_volume(self, val: VolumeDecimal):
    curr = self.get_volume()
    delta = val - curr

    # can't adjust vol by 0
    if delta > NO_DELTA:  # vol up
      self.dev.volume_up(delta)

    elif delta < NO_DELTA:
      self.dev.volume_down(abs(delta))

  def is_mute(self) -> Optional[bool]:
    if self.cast_status:
      return self.cast_status.volume_muted

    return False

  def set_mute(self, val: bool):
    self.dev.set_volume_muted(val)


class AbilitiesMixin(Wrapper):
  def can_quit(self) -> bool:
    return True

  def can_play(self) -> bool:
    state = self.get_playstate()

    if state is not PlayState.PLAYING:
      return True

    return False

  def can_control(self) -> bool:
    return True
    #return self.can_play() or self.can_pause() or \
      #self.can_play_next() or self.can_play_prev() or \
      #self.can_seek()

  def can_edit_track(self) -> bool:
    return False

  def can_play_next(self) -> bool:
    if self.media_status:
      return self.media_status.supports_queue_next

    return False

  def can_play_prev(self) -> bool:
    if self.media_status:
      return self.media_status.supports_queue_prev

    return False

  def can_pause(self) -> bool:
    if self.media_status:
      return self.media_status.supports_pause

    return False

  def can_seek(self) -> bool:
    if self.media_status:
      return self.media_status.supports_seek

    return False


class DeviceWrapper(
  StatusMixin,
  TitlesMixin,
  ControllersMixin,
  TimeMixin,
  IconsMixin,
  MetadataMixin,
  PlaybackMixin,
  VolumeMixin,
  AbilitiesMixin,
):
  '''Wraps implementation details for device API'''

  def __init__(self, dev: Chromecast):
    self.dev = dev
    super().__init__()

  def __repr__(self) -> str:
    cls = type(self)
    cls_name = cls.__name__

    return f'<{cls_name} for {self.dev}>'

  @property
  def name(self) -> str:
    return self.dev.name or DEFAULT_NAME


@enforce_dbus_length
def get_track_id(name: str) -> DbusObj:
  return f'/track/{get_dbus_name(name)}'


def get_media_type(
  dev: DeviceWrapper
) -> Optional[MediaType]:
  status = dev.media_status

  if not status:
    return None

  if status.media_is_movie:
    return MediaType.MOVIE

  elif status.media_is_tvshow:
    return MediaType.TVSHOW

  elif status.media_is_photo:
    return MediaType.PHOTO

  elif status.media_is_musictrack:
    return MediaType.MUSICTRACK

  elif status.media_is_generic:
    return MediaType.GENERIC

  return None


def is_youtube(uri: str) -> bool:
  uri = uri.lower()
  return any(yt in uri for yt in YOUTUBE_URLS)


def get_video_id(uri: str) -> Optional[str]:
  if not is_youtube(uri):
    return None

  video_id: Optional[str] = None

  if YT_LONG in uri:
    *_, video_id = uri.split('v=')

  elif YT_SHORT in uri:
    *_, video_id = uri.split('/')

  if video_id and '&' in video_id:
    video_id, *_ = video_id.split('&')

  return video_id
