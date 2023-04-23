import libtorrent as lt
import os
import csv
import time
import ntplib
import tempfile
import logging
from datetime import datetime, timezone, timedelta


class Client():
    def __init__(self):
        logging.basicConfig(level=logging.INFO)
        # 重複するピアを記録する必要はないため、集合として定義
        self.peer_info = set()
        self.logger = logging.getLogger(__name__)

    def add_peer_info(self, torrent_handle):
        """
        torrent_handleに含まれるピア情報を記録する。

        Parameters
        ----------
        torrent_handle : torrent_handle
            ピア情報を記録する対象のtorrent_handle。
        """
        for p in torrent_handle.get_peer_info():
            self.peer_info.add(p.ip)

    def download(self, torrent_path, save_path):
        """
        指定した.torrentファイルをもとに本体ファイルをダウンロードする。

        Parameters
        ----------
        torrent_path : str
            ダウンロードを行うtorrentファイルへのパス。
        save_path : str
            本体ファイルのダウンロード先のパス。
        """

        session = lt.session({'listen_interfaces': '0.0.0.0:6881'})

        info = lt.torrent_info(torrent_path)
        handle = session.add_torrent({'ti': info, 'save_path': save_path})

        self.logger.info('starting %s', handle.status().name)

        while not handle.status().is_seeding:
            _print_download_status(handle.status(), handle.get_peer_info(), self.logger)
            self.add_peer_info(handle)
            time.sleep(1)

        with open(os.path.join(save_path, 'peer.csv'), mode='a', newline='') as csvfile:
            writer = csv.writer(csvfile)
            for ip in self.peer_info:
                writer.writerow([ip[0], ip[1]])

        self.logger.info('complete %s', handle.status().name)
        self.logger.info("File Hash: %s, File size: %d, Time: %s" % (
            handle.info_hash(), info.total_size(), _fetch_jst().strftime('%Y-%m-%d %H:%M:%S')))

    def download_piece(self, torrent_path, save_path, piece_index, peer):
        """
        指定した.torrentファイルからひとつのピースをダウンロードする。

        Parameters
        ----------
        torrent_path : str
            .torrentファイルへのパス。
        save_path : str
            ファイルの保存場所のパス。
        piece_index : int
            ダウンロードしたいピースのindex。
        peer : (str, int)
            ピースをダウンロードするピア。
        """

        session = lt.session({'listen_interfaces': '0.0.0.0:6881'})

        # 指定されたピアのみからダウンロードするために、ipフィルタを作成する
        ip_filter = lt.ip_filter()

        # まずすべてのアドレスを禁止してから、引数で指定したアドレスのみ許可。
        # 第三引数の0は許可するアドレス指定、1は禁止するアドレス指定
        ip_filter.add_rule('0.0.0.0', '255.255.255.255', 1)
        ip_filter.add_rule(peer[0], peer[0], 0)

        self.logger.info(ip_filter.export_filter())

        session.set_ip_filter(ip_filter)

        info = lt.torrent_info(torrent_path)

        with tempfile.TemporaryDirectory() as tmpdir:
            handle = session.add_torrent({'ti': info, 'save_path': tmpdir})

            # 指定したindexのみpriorityを非ゼロにする。
            # その他はpriority=0にする（ダウンロードしない）。
            pp = [0]*info.num_pieces()
            pp[piece_index] = 1
            handle.prioritize_pieces(pp)

            self.__wait_for_download(session, handle, piece_index, 10)

            handle.read_piece(piece_index)

            # msで指定する
            session.wait_for_alert(1000)
            alerts = session.pop_alerts()
            for a in alerts:
                if isinstance(a, lt.read_piece_alert):
                    self.logger.info('piece read')
                    _write_piece_to_file(a.buffer, os.path.join(
                        save_path, '{:05}_{}_{}_{}.bin'.format(piece_index, peer[0], peer[1], info.name())))

    def __wait_for_download(self, session, torrent_handle, piece_index, max_retries):
        retry_counter = 0

        while not torrent_handle.status().pieces[piece_index]:
            # torrent_handle.status().piecesの戻り値はboolの配列なので、この条件で判定できる
            _print_download_status(torrent_handle.status(), torrent_handle.get_peer_info(), self.logger)

            # alertの出力を行う
            alerts = session.pop_alerts()
            for a in alerts:
                if a.category() & lt.alert.category_t.error_notification:
                    self.logger.warn(a)

            time.sleep(1)

            if torrent_handle.status().num_peers == 0:
                retry_counter += 1

            if retry_counter >= max_retries:
                self.logger.warn('Max retries exceeded')
                break


def _print_download_status(torrent_status, peer_info, logger):
    logger.info(
        "downloading: %.2f%% complete (down: %.1f kB/s, up: %.1f kB/s, peers: %d) %s" % (
            torrent_status.progress * 100,
            torrent_status.download_rate / 1000,
            torrent_status.upload_rate / 1000,
            len(peer_info), torrent_status.state)
    )


def _write_piece_to_file(piece, save_path):
    """
    ピースを指定されたパスに書き込む.

    Parameters
    ----------
    piece : bytes
        ピースのバイト列。

    save_path : str
        保存先ファイルのパス。
    """
    dir = os.path.dirname(save_path)
    os.makedirs(dir, exist_ok=True)
    with open(save_path, 'wb') as f:
        f.write(piece)


def _fetch_jst():
    """
    NTPサーバからUNIX時刻を取得し、JSTに変換して返却する。

    Returns
    -------
    jst_time: datetime
        JSTを表すdatetime。
    """
    # NTPサーバのアドレスを指定する
    ntp_server = 'ntp.nict.jp'

    # NTPサーバからUNIX時刻を取得する
    ntp_client = ntplib.NTPClient()
    response = ntp_client.request(ntp_server)
    unix_time = response.tx_time

    # UNIX時刻をJSTに変換する
    jst = timezone(timedelta(hours=+9), 'JST')
    jst_time = datetime.fromtimestamp(unix_time, jst)

    return jst_time
