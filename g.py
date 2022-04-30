import yolo_app
import os
import time
import sys
from gevent import socket
from toolz import mapcat as flatmap
from e_helper import *
from typing import List, TypeVar
import hy
import logging
# https://docs.python.org/3/howto/logging.html

pwd = os.path.dirname(os.path.realpath(__file__))
print(pwd)
so_root = os.path.join(pwd, 'cmake-build-debug')
print(so_root)
sys.path.insert(0, so_root)

base_rtmp_url = "rtmp://localhost:1935/live/"

opts_dict = {
  "input_file_path": "0",
  # "input_file_path": os.path.join(so_root, "test.mp4"),
  "output_file_path": "",
  "param_path": os.path.join(pwd, "model", "yolo-fastestv2-opt.param"),
  "bin_path": os.path.join(pwd, "model", "yolo-fastestv2-opt.bin"),
  "rtmp_url": base_rtmp_url + "test",
  "redis_url": "tcp://127.0.0.1:6379",
  "scaled_coeffs": 0.2,
  "threshold_NMS": 0.125,
  "out_fps": 5,
  "crop_coeffs": 0.1,
  "threads_num": 4,
  "is_debug": True,
}

base_pipeline = "appsrc ! " + \
                "videoconvert ! " + \
                "x264enc  pass=5 quantizer=25 speed-preset=6 ! " + \
                "video/x-h264, profile=baseline ! " + \
                "flvmux ! " + \
                "rtmpsink location="


# https://www.gevent.org/examples/udp_client.html
# https://www.gevent.org/examples/udp_server.html

# stream
# make sure server and client are share the same port

T = TypeVar("T")

def drop (coll: list[T], n: int=1)->list[T]:
  return coll[n:]

def take (coll: list[T], n: int=1)->list[T]:
  return coll[:n]

def bytes_to_uint_str(b: bytes)->str:
  return str(int.from_bytes(b, byteorder='big', signed=False))


def int_to_bytes(x: int, n=2):
  """big endian"""
  return x.to_bytes(n, byteorder='big')

def gen_init_msg(id: int):
  """id should be int. this function will convert it to bytes"""
  id_bytes = int_to_bytes(id)
  return MsgType.INIT.value.to_bytes(1, 'big', signed = False) + id_bytes

class UDPApp:
  def __init__(self, remote_host: str, remote_port: int, yolo_app, id: int):
    self.id = id
    self.host = remote_host
    self.port = remote_port
    address = (remote_host, remote_port)
    # yolo_app is MainWrapper
    self.hash:   None | bytes = None
    self.e_chn:  None | bytes = None
    self.app = yolo_app
    self.sock = socket.socket(type=socket.SOCK_DGRAM)
    self.sock.connect(address)

  def on_detect_yolo(self, xs):
    if xs:
      for x in xs:
        # pts = [x.x1, x.y1, x.x2, x.y2]
        # byte_list = bytes.fromhex("70") + \
        #     bytes(flatmap(lambda x: x.to_bytes(
        #         2, 'big', signed=True), pts))
        logging.debug("[yolo] ({},{}) ({},{}) category: {} score: {}"
                      .format(x.x1, x.y1, x.x2, x.y2, x.cate, x. score))
        # self.sock.send(byte_list)
        # print("[yolo] send {}".format(byte_list.hex()))

  def on_detect_door(self, xs):
    if xs:
      for x in xs:
        (x1, y1), (x2, y2) = x
        # pts = [x1, y1, x2, y2]
        # byte_list = bytes.fromhex("80") + \
        #     bytes(flatmap(lambda x: x.to_bytes(
        #         2, 'big', signed=True), pts))
        logging.debug("[door] ({},{}) ({},{})"
                      .format(x1, y1, x2, y2))
        # self.sock.send(byte_list)
        # print("[door] send {}".format(byte_list.hex()))
  
  def on_poll_complete(self, poll):
    logging.debug("poll completes! frame count {}".format(poll))

  def handle_req(self, msg: bytes):
    match list(msg):
      case [MsgType.INIT.value, *rest]:
        self.hash = bytes(take(rest, ElemLen.HASH.value))
        logging.info("set hash as {}".format(self.hash.hex()))
      case [MsgType.RTMP_EMERG.value, *rest]:
        hash = bytes(take(rest, ElemLen.HASH.value))
        rest = drop(rest, ElemLen.HASH.value)
        if (self.hash and self.hash == hash):
          self.e_chn = bytes(take(rest, ElemLen.RTMP_CHN.value))
          logging.info("set channel as {}".format(bytes_to_uint_str(self.e_chn)))
      case [MsgType.RTMP_STREAM.value, *rest]:
        hash = bytes(take(rest, ElemLen.HASH.value))
        rest = drop(rest, ElemLen.HASH.value)
        if (self.hash and self.hash == hash):
          chn = bytes(take(rest, ElemLen.RTMP_CHN.value))
          chn_s = bytes_to_uint_str(chn)
          logging.info("Receive RTMP Channel {}".format(chn_s))
          if (self.main.get_pull_task_state() == False):
            self.main.reset_poll(chn_s)
            self.main.start_poll()
            logging.info("Start RTMP to {}".format(chn_s))
            resp = bytes([MsgType.RTMP_STREAM.value]) + \
                    self.hash + \
                    bytes([Code.OK.value])
            self.sock.send(resp)
          else:
            logging.warn("Pull Task is busy")
            resp = bytes(MsgType.RTMP_STREAM.value, ) + \
                    self.hash + \
                    bytes(Code.BUSY.value)
            self.sock.send(resp)
      case _:
        logging.warn("Invalid message {}".format(msg.hex()))

  def send_init_req(self):
    self.sock.send(gen_init_msg(self.id))

  def serve_forever(self):
    # buffer size 8192 bytes
    while True:
      data, address = self.sock.recvfrom(8192)
      self.handle_req(data)
      logging.info("Msg {} from {}".format(data.hex(), address))


host = "127.0.0.1"
port = 12345

if __name__ == "__main__":
  opts = yolo_app.init_options(opts_dict)
  main = yolo_app.MainWrapper(opts)
  main.init()
  u = UDPApp(host, port, main, 123)
  # main.set_on_detect_yolo(u.on_detect_yolo)
  # main.set_on_detect_door(u.on_detect_door)
  u.send_init_req()
  main.set_pull_task_state(True)
  main.run_push()
  main.run_pull()
  u.serve_forever()
