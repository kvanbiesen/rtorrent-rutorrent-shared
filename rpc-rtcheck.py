#!/usr/bin/env python
import sys, cStringIO as StringIO
import xmlrpclib, urllib, urlparse, socket
from datetime import datetime
import os
import pwd
import getpass
import re
from urlparse import uses_netloc
uses_netloc.append('scgi')

def do_scgi_xmlrpc_request(host, methodname, params=()):
    xmlreq = xmlrpclib.dumps(params, methodname)
    xmlresp = SCGIRequest(host).send(xmlreq)
    return xmlresp

def do_scgi_xmlrpc_request_py(host, methodname, params=()):
    xmlresp = do_scgi_xmlrpc_request(host, methodname, params)
    return xmlrpclib.loads(xmlresp)[0][0]

class SCGIRequest(object):
    def __init__(self, url):
        self.url=url
        self.resp_headers=[]
    def __send(self, scgireq):
        scheme, netloc, path, query, frag = urlparse.urlsplit(self.url)
        host, port = urllib.splitport(netloc)
        if netloc:
            addrinfo = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
            assert len(addrinfo) == 1, "There's more than one? %r"%addrinfo
            sock = socket.socket(*addrinfo[0][:3])
            sock.connect(addrinfo[0][4])
        else:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(path)
        sock.send(scgireq)
        recvdata = resp = sock.recv(1024)
        while recvdata != '':
            recvdata = sock.recv(1024)
            resp += recvdata
        sock.close()
        return resp
    def send(self, data):
        "Send data over scgi to url and get response"
        scgiresp = self.__send(self.add_required_scgi_headers(data))
        resp, self.resp_headers = self.get_scgi_resp(scgiresp)
        return resp
    @staticmethod
    def encode_netstring(string):
        "Encode string as netstring"
        return '%d:%s,'%(len(string), string)
    @staticmethod
    def make_headers(headers):
        "Make scgi header list"
        return '\x00'.join(['%s\x00%s'%t for t in headers])+'\x00'
    @staticmethod
    def add_required_scgi_headers(data, headers=[]):
        "Wrap data in an scgi request,\nsee spec at: http://python.ca/scgi/protocol.txt"
        headers = SCGIRequest.make_headers([('CONTENT_LENGTH', str(len(data))),('SCGI', '1'),] + headers)
        enc_headers = SCGIRequest.encode_netstring(headers)
        return enc_headers+data
    @staticmethod
    def gen_headers(file):
        "Get header lines from scgi response"
        line = file.readline().rstrip()
        while line.strip():
            yield line
            line = file.readline().rstrip()
    @staticmethod
    def get_scgi_resp(resp):
        "Get xmlrpc response from scgi response"
        fresp = StringIO.StringIO(resp)
        headers = []
        for line in SCGIRequest.gen_headers(fresp):
            headers.append(line.split(': ', 1))
        xmlresp = fresp.read()
        return (xmlresp, headers)

class RTorrentXMLRPCClient(object):
    def __init__(self, url, methodname=''):
        self.url = url
        self.methodname = methodname
    def __call__(self, *args):
        scheme, netloc, path, query, frag = urlparse.urlsplit(self.url)
        xmlreq = xmlrpclib.dumps(args, self.methodname)
        if scheme == 'scgi':
            xmlresp = SCGIRequest(self.url).send(xmlreq)
            return xmlrpclib.loads(xmlresp)[0][0]
        elif scheme == 'http':
            raise Exception('Unsupported protocol')
        elif scheme == '':
            raise Exception('Unsupported protocol')
        else:
            raise Exception('Unsupported protocol')
    def __getattr__(self, attr):
        methodname = self.methodname and '.'.join([self.methodname,attr]) or attr
        return RTorrentXMLRPCClient(self.url, methodname)

def convert_params_to_native(params):
    "Parse xmlrpc-c command line arg syntax"
    cparams = []
    for param in params:
        if len(param) < 2 or param[1] != '/':
            cparams.append(param)
            continue
        if param[0] == 'i':
            ptype = int
        elif param[0] == 'b':
            ptype = bool
        elif param[0] == 's':
            ptype = str
        else:
            cparams.append(param)
            continue
        cparams.append(ptype(param[2:]))
    return tuple(cparams)
    
def main(argv):
    output_python=False
    if argv[0] == '-p':
        output_python=True
        argv.pop(0)
    host, methodname = argv[:2]
    respxml = do_scgi_xmlrpc_request(host, methodname, convert_params_to_native(argv[2:]))
    if not output_python:
        print respxml
    else:
        print xmlrpclib.loads(respxml)[0][0]


def is_private(url):
    pattern1='(http|https|udp):\/\/[a-z0-9-\.]+\.[a-z]{2,4}((:(\d){2,5})|).*\/an.*\?.+=.+'
    pattern2='(http|https|udp):\/\/[a-z0-9-\.]+\.[a-z]{2,4}((:(\d){2,5})|)\/.*[0-9a-z]{8,32}\/an'
    result=True if re.match(pattern1, url) or re.match(pattern2, url) else False
    return result


def check_torrent(hash):
    user = getpass.getuser()
    home_dir = pwd.getpwnam(user).pw_dir
    host = '/downloads/.rtorrent/socket'
    
    try:
        methodname = 'd.get_state'
        respxml = do_scgi_xmlrpc_request(host, methodname, convert_params_to_native(['s/{}'.format(hash)]))
        torrent_active = bool(xmlrpclib.loads(respxml)[0][0])

        methodname = 'd.get_complete'
        respxml = do_scgi_xmlrpc_request(host, methodname, convert_params_to_native(['s/{}'.format(hash)]))
        torrent_complete =  bool(xmlrpclib.loads(respxml)[0][0])

        methodname = 'd.is_private'
        respxml = do_scgi_xmlrpc_request(host, methodname, convert_params_to_native(['s/{}'.format(hash)]))
        torrent_private =  bool(xmlrpclib.loads(respxml)[0][0])

        if torrent_active and torrent_complete and not torrent_private:
            # let's do one more check using tracker URL
            methodname = 'd.get_tracker_size'
            respxml = do_scgi_xmlrpc_request(host, methodname, convert_params_to_native(['s/{}'.format(hash)]))
            tracker_cnt =  xmlrpclib.loads(respxml)[0][0]

            is_public = True
            for t in range(0, tracker_cnt):
                methodname = 't.get_url'
                respxml = do_scgi_xmlrpc_request(host, methodname, convert_params_to_native(['s/{}'.format(hash), 'i/{}'.format(t)]))
                tracker = xmlrpclib.loads(respxml)[0][0]
                #print(' [+] checking: {}'.format(tracker))
                if is_private(tracker):
                    is_public = False
                    break

            if is_public:
                methodname = 'd.try_close'
				#methodname = 'd.try_stop'
                respxml = do_scgi_xmlrpc_request(host, methodname, convert_params_to_native(['s/{}'.format(hash)]))
                #print(respxml)
                result = not bool(xmlrpclib.loads(respxml)[0][0])
                if result:
                    print('{} {} stoppped.'.format(datetime.now().isoformat(), hash))

        #debug:
        #print( torrent_active, torrent_complete, torrent_private)
    except xmlrpclib.Fault as e :
        print 'ERROR: ', e

if __name__ == "__main__":
    if len(sys.argv)>1:
        check_torrent(sys.argv[1])
    else:
        print ('Missing parameter: <hash>')
