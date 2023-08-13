#!/usr/bin/env python
# coding=utf-8
import configparser
import json
import math
import uuid
import ctypes
import time
import socket
import threading
import aiohttp_cors
import asyncpg
import asyncio
import base64
import inspect
import hashlib
import http.client
import pymysql
from aiohttp import web
import urllib.request
# sfrom asycpg.exceptions import UndefinedColumnError
from web3.middleware import geth_poa_middleware
from src.my_redis import MyRedis
from tronpy.keys import to_base58check_address
import io
import sys
import logging
import random

logging.basicConfig(filename='errLog.txt', level=logging.WARNING,
                    format='%(asctime)s %(levelname)s %(name)s %(message)s')
logger = logging.getLogger(__name__)

# sys.stdout = io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8')

AllThreads = []
routes = web.RouteTableDef()
config = configparser.ConfigParser()
config.read('src/conf/config.ini')

mode = 'LOCAL'

rd = MyRedis(config[mode]['redis_address'], int(config[mode]['redis_port']), config[mode]['redis_password'])
expiration_time = config[mode]['expiration_time']

namedb = []

CONN = None
_page = 1


## DB Stuffs
def fetch(query):
    connection = pymysql.connect(
        user=config[mode]['db_user'],
        password=config[mode]['db_password'],
        port=int(config[mode]['db_port']),
        host=config[mode]['db_host'],
        db=config[mode]['db_name'],
        charset="utf8"
    )
    cursor = connection.cursor()
    cursor.execute(query)
    result = cursor.fetchall()
    connection.close()
    return result


def fetchrow(query):
    connection = pymysql.connect(
        user=config[mode]['db_user'],
        password=config[mode]['db_password'],
        port=int(config[mode]['db_port']),
        host=config[mode]['db_host'],
        db=config[mode]['db_name'],
        charset="utf8"
    )
    cursor = connection.cursor()
    cursor.execute(query)
    result = cursor.fetchone()
    connection.close()
    return result


def execute(query):
    connection = pymysql.connect(
        user=config[mode]['db_user'],
        password=config[mode]['db_password'],
        port=int(config[mode]['db_port']),
        host=config[mode]['db_host'],
        db=config[mode]['db_name'],
        charset="utf8"
    )
    cursor = connection.cursor()
    cursor.execute(query)
    connection.close()
    return cursor


def address_handle(address):
    _len = len(address)
    _address = address[0:10] + '...' + address[_len - 12:_len - 1]
    return _address


def get_host_ip(request):
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]  # 多次反向代理后会有多个ip值，第一个ip才是真实ip
    else:
        ip = request.META.get('REMOTE_ADDR')  # 这里获得代理ip
    return ip


# 定时读取区块交易，每2分钟执行
async def thread_btc_block():
    while 1:
        print("--- thread_btc_block ---")
        # 获取最新的区块
        last_block = rd.get('BTC_last_block')
        if last_block is None:
            last_block = int(config[mode]['BTC_start_block_height'])
        else:
            last_block = int(last_block)
        last_block = last_block + 1

        # 读取所有地址
        our_address = json.loads(rd.get('our_address_BTC'))
        if our_address is None or len(our_address) < 1:
            rd.set('BTC_last_block', last_block)
            time.sleep(120)
            continue

        apiKey = random.choice(config[mode]["apikey_blockcypher_com"].split(","))
        url = "http://api.blockcypher.com/v1/btc/main/blocks/" + str(last_block) + "?token=" + str(apiKey) + "&limit=1"
        print(url)
        try:
            response = urllib.request.urlopen(url, timeout=15)
            rt = response.read().decode('utf-8')
            if rt:
                data = json.loads(rt)
                _hash = data["hash"]
                # data_sj = time.strptime(data.received_time, "%Y-%m-%dT%H:%M:%SZ")
                # timestamp = int(time.mktime(data_sj)) * 1000
                print("https://api.blockchain.info/haskoin-store/btc/block/" + str(_hash) + "?notx=false")
                response2 = urllib.request.urlopen(
                    "https://api.blockchain.info/haskoin-store/btc/block/" + str(_hash) + "?notx=false", timeout=15)
                rt2 = response2.read().decode('utf-8')
                if rt2:
                    data2 = json.loads(rt2)
                    txs = data2["tx"]
                    a = json.dumps(txs)
                    b = a[1:len(a) - 2]
                    c = b.replace("\"", "")
                    txids = c.replace(" ", "")
                    btc_tx_request(our_address, last_block, txids, 0)
                else:
                    print('[BTC]this block height has not tx data!')
            else:
                print('[BTC]no request data!')

        except Exception as err:
            try:
                if "code" in err.keys() and str(err.code) != "200":
                    if str(err.code) == "404":
                        print('[BTC]this block height has not tx data!')
                    else:
                        print("This request return status code is: " + str(err.code))
                        if config[mode]["log_debug"]:
                            logger.error('line 166:')
                            logger.error("This request return status code is: " + str(err.code) + "; url=" + str(
                                url) + "; current apiKey=" + str(apiKey))
            except Exception as err:
                print("[err line:173]")

        # 休眠2分钟
        time.sleep(120)


def btc_tx_request(our_address, last_block, txids, start):
    url = None
    try:
        # GET方式不能超过2048字节
        end = start + 65 * 30
        if end > len(txids):
            end = len(txids) + 1
            is_end = True
        else:
            is_end = False
        _txids = txids[start:end - 1]
        url = "https://api.blockchain.info/haskoin-store/btc/transactions?txids=" + _txids
        # if is_end:
        #     print("https://api.blockchain.info/haskoin-store/btc/transactions?txids=" + _txids)
        response3 = urllib.request.urlopen(url, timeout=15)
        rt3 = response3.read().decode('utf-8')
        # print(rt3)
        if rt3:
            transactions = json.loads(rt3)
            # print(transactions[0])
            for transaction in transactions:
                inputs = transaction["inputs"]
                outputs = transaction["outputs"]
                timestamp = int(transaction["time"]) * 1000
                _hash = transaction["txid"]

                _type = 0
                amount = 0
                set_data = []
                address = []

                for input in inputs:
                    if input["address"] is not None and input["address"].lower() in our_address:
                        _type = 1
                        our_address = input["address"].lower()
                        break

                if _type == 0:
                    for output in outputs:
                        if output["address"] is not None and output["address"].lower() in our_address:
                            _type = 2
                            our_address = output["address"].lower()
                            break

                # 计算金额
                for input in inputs:
                    if input["value"] is not None:
                        amount += int(input["value"])
                    if _type == 2:
                        address.append(input["address"])

                for output in outputs:
                    if output["value"] is not None:
                        amount -= int(output["value"])
                    if _type == 1:
                        address.append(output["address"])

                if _type > 0 and len(address) > 0 and our_address is not None:
                    a = json.dumps(address)
                    b = a[1:len(a) - 2]
                    c = b.replace("\"", "")
                    addressStr = c.replace(" ", "")
                    _status = 'ok'
                    amount = int(amount) / int(math.pow(10, 8))
                    set_data = ["BTC", addressStr, our_address, _hash, _type, amount, _status, ""]

                # 储存到redis
                if len(set_data) > 0:
                    rd.L_Push("txs", json.dumps({
                        "symbol": set_data[0],
                        "from": set_data[1],
                        "to": set_data[2],
                        "contract_addr": set_data[7],
                        "hash": set_data[3],
                        "type": set_data[4],
                        "timestamp": timestamp,
                        "value": set_data[5],
                        "status": set_data[6]
                    }))

            # 更新最新区块高度
            if is_end:
                if len(transactions) <= 0:
                    print("[BTC]this block height has no tx data!")

                rd.set('BTC_last_block', last_block)
            else:
                btc_tx_request(our_address, last_block, txids, end)

        else:
            print('[BTC]no request data!')
    except Exception as err:
        print(err)
        try:
            if "code" in err.keys() and str(err.code) != "200":
                print("This request return status code is: " + str(err.code))
                if config[mode]["log_debug"]:
                    logger.error('line 265:')
                    logger.error("This request return status code is: " + str(err.code) + "; url=" + str(url))
        except Exception as err:
            print("[err line:280]")


# 定时读取区块交易，每3秒执行
async def thread_dot_block():
    while 1:
        print("--- thread_dot_block ---")
        # 获取最新的区块
        last_block = rd.get('DOT_last_block')
        if last_block is None:
            last_block = int(config[mode]['DOT_start_block_height'])
        else:
            last_block = int(last_block)
        last_block = last_block + 1
        # 读取所有地址

        our_address = json.loads(rd.get('our_address_DOT'))
        if our_address is None or len(our_address) < 1:
            rd.set('DOT_last_block_' + str(n), last_block)
            time.sleep(3)
            continue

        try:
            # print("https://api.dotscanner.com/blocks/"+str(last_block)+"?chain=Polkadot")
            # response = urllib.request.urlopen("https://api.dotscanner.com/blocks/"+str(last_block)+"?chain=Polkadot",timeout=15)
            print("https://explorer-32.polkascan.io/api/v1/polkadot/block/" + str(
                last_block) + "?include=transactions,inherents")
            response = urllib.request.urlopen(
                "https://explorer-32.polkascan.io/api/v1/polkadot/block/" + str(last_block) + "?include=transactions",
                timeout=15)
            rt = response.read().decode('utf-8')
            if rt:
                data = json.loads(rt)
                if data:
                    if "error" in data.keys():
                        print(data["error"])
                        # 更新最新区块高度
                        rd.set('DOT_last_block', last_block)
                        continue
                    transactions = data["included"]
                    datetime = data["data"]["attributes"]["datetime"]
                    for transaction0 in transactions:
                        transaction = transaction0["attributes"]
                        params = transaction["params"]
                        amount = 0
                        _to = None
                        for param in params:
                            if param["type"].lower() == 'balance':
                                amount = param["value"]
                            if param["type"].lower() == 'lookupsource':
                                _to = param["value"].lower()
                        _from = transaction["address"].lower()
                        if "success" in transaction.keys() and transaction["success"] == 1:
                            _status = "ok"
                        else:
                            _status = "not ok"
                        _hash = transaction0["id"]
                        # 时间戳转化
                        timeStrAfter = datetime[len(datetime) - 5:len(datetime)]
                        data_sj = time.strptime(datetime, "%Y-%m-%dT%H:%M:%S+" + timeStrAfter)  # 定义格式
                        timestamp = int(time.mktime(data_sj)) * int(math.pow(10, 3))

                        try:
                            amount = int(amount)
                            if amount > 0:
                                amount = amount / int(math.pow(10, 10))
                        except Exception as err:
                            print("[DOT 577]")
                            print(err)
                            if config[mode]["log_debug"]:
                                logger.error('line 327:')
                                logger.error(err)

                        set_data = []
                        if _from is not None and _from.lower() in our_address:
                            # 转账（转出）交易
                            print("DOT转账（转出）交易")
                            set_data.append(["DOT", _from, _to, _hash, 1, amount, _status, ""])
                        if _to is not None and _to.lower() in our_address:
                            # 收款（转入）交易
                            print("DOT收款（转入）交易")
                            set_data.append(["DOT", _from, _to, _hash, 2, amount, _status, ""])

                        # 储存到redis
                        if len(set_data) > 0:
                            for obj in set_data:
                                rd.L_Push("txs", json.dumps({
                                    "symbol": obj[0],
                                    "from": obj[1],
                                    "to": obj[2],
                                    "contract_addr": obj[7],
                                    "hash": obj[3],
                                    "type": obj[4],
                                    "timestamp": timestamp,
                                    "value": obj[5],
                                    "status": obj[6]
                                }))

                    if len(transactions) <= 0:
                        print("[DOT]this block height has no tx data!")

                    # 更新最新区块高度
                    rd.set('DOT_last_block', last_block)
                else:
                    print('[DOT]this block height is not exit')

            else:
                print('[DOT]no request data!')

        except Exception as err:
            print("[DOT 616]")
            print(err)
            if config[mode]["log_debug"]:
                logger.error('line 370:')
                logger.error(err)

            if str(err) == "HTTP Error 302: Moved Temporarily":
                # 查看最新区块高度
                url = 'https://polkadot.webapi.subscan.io/api/scan/metadata'
                response = urllib.request.urlopen(url, timeout=15)
                rt = response.read().decode('utf-8')
                if rt:
                    data = json.loads(rt)
                    if "code" in data.keys() and data["code"] == 0 and "data" in data.keys():
                        finalized_blockNum = int(data["data"]["finalized_blockNum"])
                        if int(last_block) < finalized_blockNum:
                            print("skip this height")
                            rd.set('DOT_last_block', last_block)
                        else:
                            print("this height has not exitd")

        # 休眠3秒
        time.sleep(3)


# 得到交易列表
def trx_txs(last_block, n, start=0, data=[]):
    # url = "https://api.trongrid.io/wallet/gettransactioninfobyblocknum"
    ips = json.loads(config[mode]['TRX_thread_ips'])
    url = "http://" + ips[
        n - 1] + "/apiasia.tronscan.io:5566/api/transaction?sort=-timestamp&count=true&limit=50&start=" + str(
        start * 50) + "&block=" + str(last_block)
    try:
        if start == 0:
            print("[thread_" + str(n) + "]" + url)
        response = urllib.request.urlopen(url, timeout=15)
        rt = response.read().decode('utf-8')
        if rt:
            rData = json.loads(rt)
            if "data" in rData.keys() and "wholeChainTxCount" in rData.keys():
                _data = rData["data"]
                # lastIndex = 0
                # if len(_data) > 0:
                #     lastIndex = len(_data)-1
                # if start == 0 and len(_data)>0 and "confirmed" in _data[lastIndex].keys() and _data[lastIndex]["confirmed"] is not True:
                #     # 该区块未确认
                #     return -2

                if len(data) > 0:
                    _new_data = data + _data
                else:
                    _new_data = _data

                if len(_data) == 0:
                    return _new_data
                elif len(_data) > 0:
                    return trx_txs(last_block, n, start + 1, _new_data)
            else:
                if start == 0:
                    return -1
                else:
                    return data
    except Exception as err:
        if "502" in str(err):
            # 高度不存在
            return -1
        else:
            if config[mode]["log_debug"]:
                logger.error('[' + str(n) + ']line 426:')
                logger.error(err)
                logger.error(url)
            return -3  # 异常


# 定时读取区块交易，一直执行
async def thread_trx_block(n):
    while 1:
        try:
            print("--- thread_trx_block_" + str(n) + " ---")
            # 获取最新的区块
            last_block = rd.get('TRX_last_block_' + str(n))
            if last_block is None:
                last_block = int(config[mode]['TRX_start_block_height'])
            else:
                last_block = int(last_block)
            TRX_thread_sum = int(config[mode]['TRX_thread_sum'])
            last_block = last_block + TRX_thread_sum

            # 读取所有地址
            our_address = json.loads(rd.get('our_address_TRX'))
            if our_address is None or len(our_address) < 1:
                rd.set('TRX_last_block_' + str(n), last_block)
                time.sleep(1)
                continue

            try:
                # 得到交易列表
                transactions = trx_txs(last_block, n)
                if transactions == -1:
                    print('[TRX 477]not exit the block height')
                    time.sleep(1)
                    continue
                # if transactions == -2:
                #     print('[TRX 480]this block not confirmed!')
                #     continue
                if transactions == -3:
                    print('[TRX 442]cacth!')
                    time.sleep(1)
                    continue
                # print('transactions_len='+str(len(transactions)))

                # 交易遍历
                set_data = []
                has_unconfirmed_block = False
                for transaction in transactions:
                    value = transaction["amount"]
                    _to = transaction["toAddress"]
                    _from = transaction["ownerAddress"]
                    _hash = transaction["hash"]
                    # if transaction["confirmed"]: # 要已确认的

                    if "result" in transaction.keys() and transaction["result"] == "SUCCESS":
                        _status = "ok"
                    else:
                        _status = "not ok"

                    contractData = transaction["contractData"]
                    timestamp = transaction["timestamp"]
                    amount = 0

                    if str(value).find("0x") == 0:
                        value = int(value, 16)

                    if int(value) > 0:
                        amount = int(value) / int(math.pow(10, 6))

                    # print(contractData)
                    if contractData is not None and "contract_address" in contractData.keys() and "data" in contractData.keys() and "owner_address" in contractData.keys() and len(
                            contractData["contract_address"]) > 10 and contractData["owner_address"] is not None:
                        if contractData["data"][0:8] in ["a9059cbb", "d2d745b1"]:  # transfer方法、multisendToken方法
                            token_data = getAmount(contractData["data"], 'TRX')
                            if token_data is None:
                                continue
                            token_amount = token_data[2]
                            try:
                                to_address = to_base58check_address(token_data[1])  # 转换成base58地址
                            except Exception as err:
                                print("[TRX 506]")
                                print(err)
                                if config[mode]["log_debug"]:
                                    logger.error('line 504:')
                                    logger.error(err)
                                # 异常跳出本次任务
                                continue
                            # 合约转账
                            if contractData["owner_address"].lower() in our_address:
                                # 代币转账（转出）交易
                                print("TRX Token transfer (transfer out) transaction")
                                set_data.append(["TRX-TOKEN", _from, to_address, _hash, 1, token_amount, _status, _to])
                            elif to_address.lower() in our_address:
                                # 代币收款（转入）交易
                                print("TRX Token collection (transfer in) transaction")
                                set_data.append(["TRX-TOKEN", _from, to_address, _hash, 2, token_amount, _status, _to])
                            # print(set_data)
                    else:
                        # 是主网币转账 print("是主网币转账")
                        if _from is not None and _from.lower() in our_address:
                            # 主网币转账（转出）交易
                            print("TRX Main network currency transfer (transfer out) transaction")
                            set_data.append(["TRX", _from, _to, _hash, 1, amount, _status, ""])
                        # 存在转入、转出地址都是自家地址的情况
                        elif _to is not None and _to.lower() in our_address:
                            # 主网币收款（转入）交易
                            print("TRX Main network currency collection (transfer in) transaction")
                            set_data.append(["TRX", _from, _to, _hash, 2, amount, _status, ""])

                    # else:
                    #     # 有没确认的区块，需要再次轮询
                    #     has_unconfirmed_block = True

                # 储存到redis
                if has_unconfirmed_block is False:
                    if set_data is not None and len(set_data) > 0:
                        for obj in set_data:
                            rd.L_Push("txs", json.dumps({
                                "symbol": obj[0],
                                "from": obj[1],
                                "to": obj[2],
                                "contract_addr": obj[7],
                                "hash": obj[3],
                                "type": obj[4],
                                "timestamp": timestamp,
                                "value": obj[5],
                                "status": obj[6],
                                "thread": str(n),
                                "height": last_block
                            }))

                # 更新最新区块高度 该区块需要已确认才行
                if has_unconfirmed_block is False and len(transactions) > 0:
                    rd.set('TRX_last_block_' + str(n), last_block)
                else:
                    if len(transactions) > 0:
                        if has_unconfirmed_block is True:
                            print("[TRX][" + str(last_block) + "]this block unconfirmed txs!")
                        else:
                            print("[TRX][" + str(last_block) + "]this block isn't confirmed!")
                    else:
                        print('[TRX][' + str(last_block) + ']this block height has not tx data!')
                        rd.set('TRX_last_block_' + str(n), last_block)

            except Exception as err:
                print("[TRX 573]")
                print(err)
                if config[mode]["log_debug"]:
                    logger.error('line 569:')
                    logger.error(err)
        except Exception as err:
            print("[TRX 576]")
            print(err)
            if config[mode]["log_debug"]:
                logger.error('line 575:')
                logger.error(err)

        # 休眠
        # time.sleep(1)


# 定时读取区块交易，一直执行
async def thread_matic_block():
    while 1:
        print("--- thread_matic_block ---")
        # 获取最新的区块
        last_block = rd.get('MATIC_last_block')
        if last_block is None:
            last_block = int(config[mode]['MATIC_start_block_height'])
        else:
            last_block = int(last_block)
        last_block = last_block + 1

        our_address = json.loads(rd.get('our_address_MATIC'))
        if our_address is None or len(our_address) < 1:
            rd.set('MATIC_last_block' + str(n), last_block)
            time.sleep(1)
            continue

        apiKey = random.choice(config[mode]["apikey_matic_scan"].split(","))
        try:
            last_block_hex = hex(last_block)
            url = "https://api.polygonscan.com/api?module=proxy&action=eth_getBlockByNumber&tag=" + last_block_hex + "&boolean=true&apikey=" + apiKey
            print(url)
            response = urllib.request.urlopen(url, timeout=15)
            rt = response.read().decode('utf-8')
            if rt:
                data = json.loads(rt)
                result = data["result"]
                if ("status" in data.keys() and data["status"] == 0) or (
                        "message" in data.keys() and data["message"] == "NOTOK"):
                    if config[mode]["log_debug"]:
                        logger.error('line 610:')
                        logger.error(data)
                    continue
                if result is not None and "transactions" in result.keys():
                    transactions = result["transactions"]
                    timestamp = int(result["timestamp"], 16) * 1000
                    for transaction in transactions:
                        value = transaction["value"]
                        _to = transaction["to"]
                        _from = transaction["from"]
                        _hash = transaction["hash"]
                        _status = "ok"
                        input_data = transaction["input"]
                        amount = 0

                        if str(value).find("0x") == 0:
                            value = int(value, 16)

                        if int(value) > 0:
                            amount = int(value) / int(math.pow(10, 18))

                        set_data = []
                        if value > 0 and len(input_data) < 10:
                            # 是主网币转账
                            if _from is not None and _from.lower() in our_address:
                                # 主网币转账（转出）交易
                                print("MATIC Main network currency transfer (transfer out) transaction")
                                set_data.append(["MATIC", _from, _to, _hash, 1, amount, _status, ""])
                            # 存在转入、转出地址都是自家地址代情况
                            if _to is not None and _to.lower() in our_address:
                                # 主网币收款（转入）交易
                                print("MATICMain network currency collection (transfer in) transaction")
                                set_data.append(["MATIC", _from, _to, _hash, 2, amount, _status, ""])
                        elif input_data and input_data[0:10] == "0xa9059cbb" and _to is not None and len(_to) > 10:
                            token_data = getAmount(input_data, 'MATIC')
                            if token_data is None:
                                continue
                            token_amount = token_data[2]
                            # 合约转账 0xa9059cbb：transfer方法
                            if _from is not None and _from.lower() in our_address:
                                # 代币转账（转出）交易
                                print("MATIC Token transfer (transfer out) transaction")
                                set_data.append(
                                    ["MATIC-TOKEN", _from, token_data[1], _hash, 1, token_amount, _status, _to])
                            if token_data[1] is not None and token_data[1].lower() in our_address:
                                # 代币收款（转入）交易
                                print("MATIC Token collection (transfer in) transaction")
                                set_data.append(
                                    ["MATIC-TOKEN", _from, token_data[1], _hash, 2, token_amount, _status, _to])

                        # 储存到redis
                        if set_data is not None and len(set_data) > 0:
                            for obj in set_data:
                                rd.L_Push("txs", json.dumps({
                                    "symbol": obj[0],
                                    "from": obj[1],
                                    "to": obj[2],
                                    "contract_addr": obj[7],
                                    "hash": obj[3],
                                    "type": obj[4],
                                    "timestamp": timestamp,
                                    "value": obj[5],
                                    "status": obj[6]
                                }))

                    # 更新最新区块高度
                    if len(transactions) <= 0:
                        print('[MATIC]this block height has not tx data!')
                    rd.set('MATIC_last_block', last_block)
                else:
                    print('[MATIC]not exit the block height')
            else:
                print('[MATIC]no request Data')

        except Exception as err:
            print("[MATIC 677]")
            print(err)
            if config[mode]["log_debug"]:
                logger.error('line 681:')
                logger.error(err)
                logger.error("line 704 current apiKey:" + str(apiKey))

        # 休眠3秒
        # time.sleep(3)


# 定时读取区块交易，一直执行
async def thread_bsc_block(n):
    while 1:
        print("--- thread_bsc_block_" + str(n) + " ---")
        # 获取最新的区块
        last_block = rd.get('BSC_last_block_' + str(n))
        if last_block is None:
            last_block = int(config[mode]['BSC_start_block_height'])
        else:
            last_block = int(last_block)
        BSC_thread_sum = int(config[mode]['BSC_thread_sum'])
        last_block = last_block + BSC_thread_sum

        our_address = json.loads(rd.get('our_address_BNB'))
        if our_address is None or len(our_address) < 1:
            rd.set('BSC_last_block_' + str(n), last_block)
            time.sleep(1)
            continue

        apiKey = random.choice(config[mode]["apikey_bsc_scan"].split(","))
        try:
            last_block_hex = hex(last_block)
            url = "https://api.bscscan.com/api?module=proxy&action=eth_getBlockByNumber&tag=" + last_block_hex + "&boolean=true&apikey=" + apiKey
            print(url)
            response = urllib.request.urlopen(url, timeout=15)
            rt = response.read().decode('utf-8')
            if rt is not None:
                data = json.loads(rt)
                # logger(data)
                result = data["result"]
                if ("status" in data.keys() and data["status"] == 0) or (
                        "message" in data.keys() and data["message"] == "NOTOK"):
                    if config[mode]["log_debug"]:
                        logger.error('line 724:')
                        logger.error(data)
                    continue
                if result is not None and "transactions" in result.keys():
                    transactions = result["transactions"]
                    timestamp = int(result["timestamp"], 16) * 1000
                    for transaction in transactions:
                        try:
                            value = transaction["value"]
                            _to = transaction["to"]
                            _from = transaction["from"]
                            _hash = transaction["hash"]
                            _status = "ok"
                            input_data = transaction["input"]
                            amount = 0

                            if str(value).find("0x") == 0:
                                value = int(value, 16)

                            if int(value) > 0:
                                amount = int(value) / int(math.pow(10, 18))

                            set_data = []
                            if value > 0 and len(input_data) < 10:
                                # 是主网币转账
                                if _from is not None and _from.lower() in our_address:
                                    # 主网币转账（转出）交易
                                    print("BSC Main network currency transfer (transfer out) transaction")
                                    set_data.append(["BSC", _to, _from, _hash, 1, amount, _status, ""])
                                # 存在转入、转出地址都是自家地址代情况
                                if _to is not None and _to.lower() in our_address:
                                    # 主网币收款（转入）交易
                                    print("BSCMain network currency collection (transfer in) transaction")
                                    set_data.append(["BSC", _from, _to, _hash, 2, amount, _status, ""])
                            elif input_data and input_data[0:10] == "0xa9059cbb" and _to is not None and len(_to) > 10:
                                # print('input_data+++++++++++'+input_data)
                                token_data = getAmount(input_data, 'BSC')
                                if token_data is None:
                                    continue
                                token_amount = token_data[2]
                                # 合约转账 0xa9059cbb：transfer方法
                                if _from is not None and _from.lower() in our_address:
                                    # 代币转账（转出）交易
                                    print("BSC Token transfer (transfer out) transaction")
                                    set_data.append(
                                        ["BSC-TOKEN", _from, token_data[1], _hash, 1, token_amount, _status, _to])
                                if token_data[1] is not None and token_data[1].lower() in our_address:
                                    # 代币收款（转入）交易
                                    print("BSC Token collection (transfer in) transaction")
                                    set_data.append(
                                        ["BSC-TOKEN", _from, token_data[1], _hash, 2, token_amount, _status, _to])

                            # 储存到redis
                            if set_data is not None and len(set_data) > 0:
                                for obj in set_data:
                                    rd.L_Push("txs", json.dumps({
                                        "symbol": obj[0],
                                        "from": obj[1],
                                        "to": obj[2],
                                        "contract_addr": obj[7],
                                        "hash": obj[3],
                                        "type": obj[4],
                                        "timestamp": timestamp,
                                        "value": obj[5],
                                        "status": obj[6]
                                    }))
                        except Exception as err:
                            if config[mode]["log_debug"]:
                                logger.error('line 779:')
                                logger.error(err)

                    # 更新最新区块高度
                    if len(transactions) > 0:
                        rd.set('BSC_last_block_' + str(n), last_block)
                    else:
                        print('[BSC]this block height has not tx data!')
                        rd.set('BSC_last_block_' + str(n), last_block)
                else:
                    print('[BSC]not exit the block height')

            else:
                print('[BSC]no request Data')

        except Exception as err:
            print("[BSC 796]")
            print(err)
            if config[mode]["log_debug"]:
                logger.error('line 793:')
                logger.error(err)
                logger.error("line 829 current apiKey:" + str(apiKey))

        # 休眠3分钟
        # time.sleep(3)


# 定时读取区块交易，每3秒执行
async def thread_eth_block():
    while 1:
        print("--- thread_eth_block ---")
        # 获取最新的区块
        last_block = rd.get('ETH_last_block')
        if last_block is None:
            last_block = int(config[mode]['ETH_start_block_height'])
        else:
            last_block = int(last_block)
        last_block = last_block + 1

        our_address = json.loads(rd.get('our_address_ETH'))
        if our_address is None or len(our_address) < 1:
            rd.set('ETH_last_block', last_block)
            time.sleep(3)
            continue

        apiKey = random.choice(config[mode]["apikey_eth_scan"].split(","))
        try:
            last_block_hex = hex(last_block)
            url = "https://api.etherscan.io/api?module=proxy&action=eth_getBlockByNumber&tag=" + last_block_hex + "&boolean=true&apikey=" + apiKey
            print(url)
            response = urllib.request.urlopen(url, timeout=15)
            rt = response.read().decode('utf-8')
            # print(rt)
            if rt is not None:
                data = json.loads(rt)
                result = data["result"]
                if ("status" in data.keys() and data["status"] == 0) or (
                        "message" in data.keys() and data["message"] == "NOTOK"):
                    if config[mode]["log_debug"]:
                        logger.error('line 845:')
                        logger.error(data)
                    continue
                if result is not None and "transactions" in result.keys():
                    transactions = result["transactions"]
                    timestamp = int(result["timestamp"], 16) * 1000
                    # print("transactions_len=" + str(len(transactions)))
                    for transaction in transactions:
                        value = transaction["value"]
                        _to = transaction["to"]
                        _from = transaction["from"]
                        _hash = transaction["hash"]
                        input_data = transaction["input"]
                        amount = 0

                        if str(value).find("0x") == 0:
                            value = int(value, 16)

                        if int(value) > 0:
                            amount = int(value) / int(math.pow(10, 18))

                        # 获取交易状态
                        _status = 'ok'

                        set_data = []
                        if value > 0 and len(input_data) < 10:
                            # 是主网币转账
                            if _from is not None and _from.lower() in our_address:
                                # 主网币转账（转出）交易
                                print("ETH Main network currency transfer (transfer out) transaction")
                                set_data.append(["ETH", _from, _to, _hash, 1, amount, _status, ""])
                            # 存在转入、转出地址都是自家地址代情况
                            if _to is not None and _to.lower() in our_address:
                                # 主网币收款（转入）交易
                                print("ETHMain network currency collection (transfer in) transaction")
                                set_data.append(["ETH", _from, _to, _hash, 2, amount, _status, ""])
                        elif input_data and input_data[0:10] == "0xa9059cbb" and _to is not None and len(_to) > 10:
                            token_data = getAmount(input_data, 'ETH')
                            if token_data is None:
                                continue
                            token_amount = token_data[2]
                            # 合约转账 0xa9059cbb：transfer方法
                            if _from is not None and _from.lower() in our_address:
                                # 代币转账（转出）交易
                                print("ETH Token transfer (transfer out) transaction")
                                set_data.append(
                                    ["ETH-TOKEN", _from, token_data[1], _hash, 1, token_amount, _status, _to])
                            if token_data[1] is not None and token_data[1].lower() in our_address:
                                # 代币收款（转入）交易
                                print("ETH Token collection (transfer in) transaction")
                                set_data.append(
                                    ["ETH-TOKEN", _from, token_data[1], _hash, 2, token_amount, _status, _to])

                        # 储存到redis
                        if set_data is not None and len(set_data) > 0:
                            for obj in set_data:
                                rd.L_Push("txs", json.dumps({
                                    "symbol": obj[0],
                                    "from": obj[1],
                                    "to": obj[2],
                                    "contract_addr": obj[7],
                                    "hash": obj[3],
                                    "type": obj[4],
                                    "timestamp": timestamp,
                                    "value": obj[5],
                                    "status": obj[6]
                                }))

                    # 更新最新区块高度
                    if len(transactions) > 0:
                        rd.set('ETH_last_block', last_block)
                    else:
                        print('[ETH]this block height has not tx data!')
                        rd.set('ETH_last_block', last_block)
                else:
                    print('[ETH]not exit the block height')
            else:
                print('[ETH]no request Data')

        except Exception as err:
            print("[ETH 917]")
            print(err)
            if config[mode]["log_debug"]:
                logger.error('line 907:')
                logger.error(err)
                logger.error("line 949 current apiKey:" + str(apiKey))

        # 休眠3分钟
        time.sleep(3)


# 定时读取未确认的交易，每2分钟执行
async def thread_eth_unconfirm_txs():
    while 1:
        print("--- thread_eth_unconfirm_txs ---")
        try:
            our_address = json.loads(rd.get('our_address_ETH'))
            if our_address is None or len(our_address) < 1:
                time.sleep(120)
                continue

            global _page
            url = 'https://eth.tokenview.com/api/pending/eth/' + str(_page) + '/50'
            print("url===" + str(url))
            response = urllib.request.urlopen(url, timeout=6)
            rt = response.read().decode('utf-8')
            # print(rt)
            if rt is not None:
                data = json.loads(rt)
                datas = data["data"]

                for obj in datas:
                    addr = obj["from"]  # 用户地址

                    # 存在我们的地址
                    if addr.lower() in our_address:
                        gas_limit = int(obj["gasLimit"])  # 整数
                        gas_price = int(obj["gasPrice"])  # 单位wei
                        nonce = int(obj["nonce"])  # 整数
                        number = float(obj["value"])  # 转账数量 处理精度后的数据
                        contract_addr = ""  # 合约地址 -可选 代币转账时必须
                        contract_decimal = ""  # 合约地址精度 -可选 代币转账时必须
                        if "tokenTransfer" in obj.keys():
                            # 合约转账
                            print("erc20 transfer")
                            token_info = obj["tokenTransfer"][0]
                            contract_addr = token_info["tokenAddr"]
                            contract_decimal = int(token_info["tokenDecimals"])
                            number = int(token_info["value"]) / math.pow(10, int(contract_decimal))
                        else:
                            # 主网转账
                            print("mainnetwork transfer")

                        # 通知php接口
                        url2 = "http://127.0.0.1/api/notice/resendTx"
                        url2 = url2 + "?addr=" + str(addr) + "&gas_limit=" + str(gas_limit) + "&gas_price=" + str(
                            gas_price) + "&nonce=" + str(nonce) + "&number=" + str(number) + "&contract_addr=" + str(
                            contract_addr) + "&contract_decimal=" + str(contract_decimal)
                        print(url2)
                        response = urllib.request.urlopen(url2, timeout=15)
                        rt = response.read().decode('utf-8')
                        if rt is not None:
                            data = json.loads(rt)
                            # print(data)
                            if int(data["code"]) == 0:
                                print("notice ok!")
                            else:
                                print("notice fail")
                        else:
                            print("notice fail2")
            else:
                print('[ETH_unconfirm]no request Data')

            _page = _page + 1

        except Exception as err:
            print("[ETH_unconfirm]")
            print(err)
            if config[mode]["log_debug"]:
                logger.error('line 980:')
                logger.error(err)
        # 休眠1秒
        time.sleep(1)


# 定时消费队列，处理交易通知，每1秒执行
async def thread_redis_spend():
    while 1:
        data = rd.BRPop("txs")
        if data is not None and len(data) > 1:
            try:
                tx_info = json.loads(data[1])
                symbolArr = tx_info["symbol"].split('-')
                networkSymbol = str(symbolArr[0]).lower()
                if networkSymbol == "bsc":
                    networkSymbol = "bnb"
                from_addr = str(tx_info["from"])
                to_address = str(tx_info["to"])
                _hash = str(tx_info["hash"])
                tx_type = str(tx_info["type"])

                my_addr = from_addr
                if tx_type == "1":  # 转出
                    my_addr = from_addr
                elif tx_type == "2":  # 转入
                    my_addr = to_address

                # 拼装链接
                url = str(config[mode][
                              "host"]) + "/api/notice/balance?network=" + networkSymbol + "&addr=" + my_addr + "&hash=" + _hash
                print(url)

                key = networkSymbol.lower() + '_' + from_addr.lower() + '_' + to_address.lower() + '_' + _hash
                # 判断是否超过最大循环执行次数 99
                if rd.get(key) is not None and int(rd.get(key)) >= 99:
                    rd.delete(key)
                    # 把这些错误的请求写入日志
                    if config[mode]["log_debug"]:
                        logger.error("Request error >=99 Times,url=" + str(url) + "; data => " + str(data[1]))
                    continue

                try:
                    response = urllib.request.urlopen(url, timeout=15)
                    rt = response.read().decode('utf-8')
                    print(rt)
                    is_ok = None  # 是否调用成功
                    if rt:
                        result = json.loads(rt)
                        if 'code' in result.keys() and int(result["code"]) == 0:
                            # 成功
                            print('------------- thread_redis_spend success --------------------')
                            is_ok = True
                            # 删除key
                            if rd.get(key) is not None:
                                rd.delete(key)

                    # 失败的时候
                    if is_ok is not True:
                        print('************* thread_redis_spend fail *************')

                        # 记录次数
                        fail_sum = 0
                        if rd.get(key):
                            fail_sum = int(rd.get(key))
                        rd.set(key, fail_sum + 1)

                        # 异常丢回去
                        print("[thread_redis_spend 980]")
                        rd.L_Push("txs", data[1])

                except Exception as err:
                    print("[thread_redis_spend 982]")
                    print(err)

                    try:
                        if "code" in err.keys() and str(err.code) != "200":
                            # 不是200就跳过
                            print("This request return status code is: " + str(err.code))
                            if config[mode]["log_debug"]:
                                logger.error('line 1099:')
                                logger.error("This request return status code is: " + str(err.code) + "; url=" + str(
                                    url) + "; data => " + str(data[1]))
                    except Exception as err:
                        print("[err line:1110]")

                    # 异常丢回去
                    rd.L_Push("txs", data[1])

            except Exception as err:
                print("[redis_spend 979]")
                print(err)
                if config[mode]["log_debug"]:
                    logger.error('line 1093:')
                    logger.error(err)
                # 推送失败，则丢回去
                rd.L_Push("txs", data[1])
        # else:
        # print('------------无待处理的通知-----------')

        # 休眠1秒
        time.sleep(1)


def update_block_height():
    apiKey0 = random.choice(config[mode]["apikey_blockcypher_com"].split(","))
    url = 'http://api.blockcypher.com/v1/btc/main?token=' + apiKey0
    response = urllib.request.urlopen(url, timeout=15)
    rt = response.read().decode('utf-8')
    if rt:
        data = json.loads(rt)
        if "height" in data.keys():
            set_last_block = int(data["height"])
            if set_last_block > 0:
                rd.set('BTC_last_block', set_last_block)
                print("BTC blockHeight update success: " + str(set_last_block))
    apiKey1 = random.choice(config[mode]["apikey_eth_scan"].split(","))
    url = 'https://api.etherscan.io/api?module=proxy&action=eth_blockNumber&apikey=' + apiKey1
    response = urllib.request.urlopen(url, timeout=15)
    rt = response.read().decode('utf-8')
    if rt:
        data = json.loads(rt)
        if "result" in data.keys() and "status" not in data.keys():
            set_last_block = int(data["result"], 16)
            if set_last_block > 0:
                rd.set('ETH_last_block', set_last_block)
                print("ETH blockHeight update success: " + str(set_last_block))
    apiKey2 = random.choice(config[mode]["apikey_bsc_scan"].split(","))
    url = 'https://api.bscscan.com/api?module=proxy&action=eth_blockNumber&apikey=' + apiKey2
    response = urllib.request.urlopen(url, timeout=15)
    rt = response.read().decode('utf-8')
    if rt:
        data = json.loads(rt)
        if "result" in data.keys() and "status" not in data.keys():
            set_last_block = int(data["result"], 16)
            if set_last_block > 0:
                BSC_thread_sum = int(config[mode]['BSC_thread_sum'])
                for i in range(BSC_thread_sum):
                    rd.set('BSC_last_block_' + str(i + 1), set_last_block + i)
                    print("BSC thread " + str(i + 1) + " blockHeight update success: " + str(set_last_block + i))
    # apiKey2 = random.choice(config[mode]["apikey_matic_scan"].split(","))
    # url = 'https://api.polygonscan.com/api?module=proxy&action=eth_blockNumber&apikey='+apiKey2
    # response = urllib.request.urlopen(url,timeout=15)
    # rt = response.read().decode('utf-8')
    # if rt:
    #     data = json.loads(rt)
    #     if "result" in data.keys() and "status" not in data.keys():
    #         set_last_block = int(data["result"],16)
    #         if set_last_block > 0:
    #             rd.set('MATIC_last_block', set_last_block)
    #             print("MATIC blockHeight update success: "+ str(set_last_block))
    url = 'https://apiasia.tronscan.io:5566/api/system/status'
    response = urllib.request.urlopen(url, timeout=15)
    rt = response.read().decode('utf-8')
    if rt:
        data = json.loads(rt)
        if "database" in data.keys():
            set_last_block = int(data["database"]["confirmedBlock"])
            if set_last_block > 0:
                TRX_thread_sum = int(config[mode]['TRX_thread_sum'])
                for i in range(TRX_thread_sum):
                    rd.set('TRX_last_block_' + str(i + 1), set_last_block + i)
                    print("TRX thread " + str(i + 1) + " blockHeight update success: " + str(set_last_block + i))
    # url = 'https://polkadot.webapi.subscan.io/api/scan/metadata'
    # response = urllib.request.urlopen(url,timeout=15)
    # rt = response.read().decode('utf-8')
    # if rt:
    #     data = json.loads(rt)
    #     if "code" in data.keys() and data["code"] == 0 and "data" in data.keys():
    #         set_last_block = int(data["data"]["blockNum"])
    #         if set_last_block > 0:
    #             rd.set('DOT_last_block', set_last_block)
    #             print("DOT blockHeight update success: "+ str(set_last_block))


def thread_update_block_height_asyncio():
    asyncio.run(update_block_height())


def update_db_data_to_redis():
    while 1:
        connection = pymysql.connect(user=config[mode]['db_user'], password=config[mode]['db_password'],
                                     port=int(config[mode]['db_port']), host=config[mode]['db_host'],
                                     db=config[mode]['db_name'], charset="utf8")
        CONN = connection.cursor()

        # 读取所有地址
        sql = "select address,network from user"
        CONN.execute(sql)
        rows = CONN.fetchall()

        our_address = {
            "BTC": [],
            "ETH": [],
            "BNB": [],
            "TRX": []
        }
        # print('rows_len='+str(len(rows)))
        for row in rows:
            if row[1] is not None and row[0] is not None:
                our_address[row[1].upper()].append(row[0].lower())
        rd.set('our_address_BTC', json.dumps(our_address["BTC"]))
        rd.set('our_address_ETH', json.dumps(our_address["ETH"]))
        rd.set('our_address_BNB', json.dumps(our_address["BNB"]))
        rd.set('our_address_TRX', json.dumps(our_address["TRX"]))
        connection.close()
        # 休眠1分钟
        time.sleep(60)


def thread_update_db_data_to_redis():
    asyncio.run(update_db_data_to_redis())


# 缓存定时清理，每10分钟执行
def job_handle_day_redis_auto_del():
    while 1:

        print('...job_handle_day_redis_auto_del is running...')
        keys = rd.AllKeys()
        for key in keys:
            if len(key) == 32:
                rt = rd.get(key)
                rt = json.loads(rt)
                try:
                    die = rt["die"]
                    if int(die) < int(time.time()):
                        # 删除
                        rd.delete(key)
                        print('【' + key + '】删除成功!')
                except Exception as inst:
                    if config[mode]["log_debug"]:
                        logger.error('line 1198:')
                        logger.error(inst)
                    print(type(inst))
                    rd.delete(key)

        # 休眠10分钟
        time.sleep(600)


def thread_redis_spend_asyncio():
    asyncio.run(thread_redis_spend())


def thread_btc_block_asyncio():
    asyncio.run(thread_btc_block())


def thread_bsc_block_asyncio(n):
    asyncio.run(thread_bsc_block(n))


def thread_eth_block_asyncio():
    asyncio.run(thread_eth_block())


def thread_eth_unconfirm_txs_asyncio():
    asyncio.run(thread_eth_unconfirm_txs())


def thread_matic_block_asyncio():
    asyncio.run(thread_matic_block())


def thread_trx_block_asyncio(n):
    asyncio.run(thread_trx_block(n))


def thread_dot_block_asyncio():
    asyncio.run(thread_dot_block())


def thread_watch_fun_asyncio():
    asyncio.run(watch_fun())


def watch_fun():
    while 1:
        MethodData = ["thread_btc_block_asyncio", "thread_eth_block_asyncio"]
        TRX_thread_sum = int(config[mode]['TRX_thread_sum'])

        for n in range(TRX_thread_sum):
            MethodData.append("thread_trx_block_asyncio_" + str(n + 1))

        _len = len(MethodData)

        for allThread in AllThreads:
            theName = str(allThread.getName())
            if allThread.is_alive():
                # print("threading "+str(theName)+": alive")
                if theName in MethodData:
                    MethodData.remove(theName)
            # else:
            #     print("--------------------- threading "+str(theName)+": no alive ---------------------")

        if _len > len(MethodData):
            for key in MethodData:
                print("key=" + str(key))
                if key == "thread_btc_block_asyncio":
                    webThread2 = threading.Thread(target=thread_btc_block_asyncio)
                    webThread2.start()
                    webThread2.setName('thread_btc_block_asyncio')
                    AllThreads.append(webThread2)
                if key == "thread_eth_block_asyncio":
                    webThread3 = threading.Thread(target=thread_eth_block_asyncio)
                    webThread3.start()
                    webThread3.setName('thread_eth_block_asyncio')
                    AllThreads.append(webThread3)
                if key.find("thread_trx_block_asyncio_") == 0:
                    m = int(key.split("thread_trx_block_asyncio_")[1])
                    webThread4 = threading.Thread(target=thread_trx_block_asyncio, args=(m,))
                    webThread4.start()
                    webThread4.setName('thread_trx_block_asyncio_' + str(m))
                    AllThreads.append(webThread4)
                if key == "thread_redis_spend_asyncio":
                    webThread8 = threading.Thread(target=thread_redis_spend_asyncio)
                    webThread8.start()
                    webThread8.setName('thread_redis_spend_asyncio')
                    AllThreads.append(webThread8)

        time.sleep(3)


def getAmount(data, coin):
    if coin == 'TRX':
        if len(data) < 136:
            return None
        method = data[0:8]
        if method.lower() == "a9059cbb":  # transfer方法
            to = data[8:72]
            amount = data[72:136]
        if method.lower() == "d2d745b1":  # multisendToken方法
            to = data[200:264]
            amount = data[328:392]
    else:
        if len(data) < 138:
            return None
        method = data[0:10]
        to = data[10:74]
        amount = data[74:138]
    to_start = 0
    amount_start = 0
    for i, obj in enumerate(to):
        if obj != "0":
            to_start = i
            break
    for j, obj in enumerate(amount):
        if obj != "0":
            amount_start = j
            break
    to = to[to_start:64]
    if len(to) < 40:
        _len = 40 - len(to)
        for m in range(_len):
            to = '0' + to
    if coin == 'TRX':
        if len(to) < 42:
            to = '41' + to
    else:
        to = '0x' + to
    amount = '0x' + amount[amount_start:64]
    return [amount, to, int(amount, 16)]


def run(debug):
    # 获取配置
    # port = int(config[mode]['port'])

    # step 0 : 初始化高度
    update_block_height()

    # 更新数据库数据到redis 减少数据库操作
    webThread00 = threading.Thread(target=thread_update_db_data_to_redis)
    webThread00.start()
    webThread00.setName('thread_update_db_data_to_redis')
    AllThreads.append(webThread00)

    # step 1 : 打开缓存清理进程
    # webThread1 = threading.Thread(target=job_handle_day_redis_auto_del)
    # webThread1.start()
    # AllThreads.append(webThread1)

    # step 2 : 打开BTC监控进程
    webThread2 = threading.Thread(target=thread_btc_block_asyncio)
    webThread2.start()
    webThread2.setName('thread_btc_block_asyncio')
    AllThreads.append(webThread2)

    # step 3 : 打开ETH监控进程
    webThread3 = threading.Thread(target=thread_eth_block_asyncio)
    webThread3.start()
    webThread3.setName('thread_eth_block_asyncio')
    AllThreads.append(webThread3)

    # # setp 3_1 : 打开ETH未确认交易
    # webThread3_1 = threading.Thread(target=thread_eth_unconfirm_txs_asyncio)
    # webThread3_1.start()
    # webThread3_1.setName('thread_eth_unconfirm_txs_asyncio')
    # AllThreads.append(webThread3_1)

    # step 4 : 打开TRX监控进程
    TRX_thread_sum = int(config[mode]['TRX_thread_sum'])
    for i in range(TRX_thread_sum):
        webThread4 = threading.Thread(target=thread_trx_block_asyncio, args=(i + 1,))
        webThread4.start()
        webThread4.setName('thread_trx_block_asyncio_' + str(i + 1))
        AllThreads.append(webThread4)

    # step 5 : 打开MATIC监控进程
    # webThread5 = threading.Thread(target=thread_matic_block_asyncio)
    # webThread5.start()
    # AllThreads.append(webThread5)

    # step 6 : 打开DOT监控进程
    # webThread6 = threading.Thread(target=thread_dot_block_asyncio)
    # webThread6.start()
    # AllThreads.append(webThread6)

    # step 7 : 打开BSC监控进程
    BSC_thread_sum = int(config[mode]['BSC_thread_sum'])
    for i in range(BSC_thread_sum):
        webThread7 = threading.Thread(target=thread_bsc_block_asyncio, args=(i + 1,))
        webThread7.start()
        AllThreads.append(webThread7)

    # step 8 : 打开redis交易消费进程
    webThread8 = threading.Thread(target=thread_redis_spend_asyncio)
    webThread8.start()
    webThread8.setName('thread_redis_spend_asyncio')
    AllThreads.append(webThread8)

    # step 9 : 添加守护进程
    # webThread9 = threading.Thread(target=thread_watch_fun_asyncio)
    # webThread9.start()

    # time.sleep(6)

    # for allThread in AllThreads:
    #     theName = str(allThread.getName())
    #     if theName == "thread_trx_block_asyncio_6":
    #         _async_raise(allThread.ident, SystemExit)


def stop():
    for theThread in AllThreads:
        if theThread.is_alive():
            _async_raise(theThread.ident, SystemExit)


def _async_raise(tid, exctype):
    """raises the exception, performs cleanup if needed"""
    tid = ctypes.c_long(tid)
    if not inspect.isclass(exctype):
        exctype = type(exctype)
    res = ctypes.pythonapi.PyThreadState_SetAsyncExc(tid, ctypes.py_object(exctype))
    if res == 0:
        raise ValueError("invalid thread id")
    elif res != 1:
        # """if it returns a number greater than one, you're in trouble,
        # and you should call it again with exc=NULL to revert the effect"""
        ctypes.pythonapi.PyThreadState_SetAsyncExc(tid, None)
    raise SystemError("PyThreadState_SetAsyncExc failed")
