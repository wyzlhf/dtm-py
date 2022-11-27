from typing import Dict, Union, Any, Tuple

import pymysql
from dtmcli import barrier, tcc, utils, saga, msg
from flask import Flask, request
from pymysql.cursors import Cursor

app = Flask(__name__)
dbconf = {'host': '124.222.54.172', 'port': '3306', 'user': 'root', 'password': 'lhf19820130'}


def conn_new() -> Cursor:
    print('正在连接数据库：', dbconf)
    return pymysql.connect(host=dbconf['host'], user=dbconf['user'], password=dbconf['password'], database='').cursor()


def barrier_from_req(request: request):
    print('调用barrier_from_req()函数')
    return barrier.BranchBarrier(request.args.get('trans_type'), request.args.get('gid'), request.args.get('branch_id'),
                                 request.args.get('op'))


# 这是dtm服务地址
dtm: str = "http://localhost:36789/api/dtmsvr"
# 这是业务微服务地址
svc: str = "http://localhost:5000/api"

out_uid: int = 1
in_uid: int = 2


@app.get('/api/fireTcc')
def fire_tcc() -> Dict[str, str]:
    print('调用fire_tcc()函数，调用路径：/api/fireTcc，调用方式：get')
    gid: str = tcc.tcc_global_transaction(dtm, utils.gen_gid(dtm), tcc_trans)
    return {'gid': gid}


def tcc_trans(t) -> None:
    print('调用tcc_trans()函数')
    req: Dict[str, int] = {'amount': 30}
    # 调用转出服务的Try|Confirm|Cancel
    t.call_branch(req, svc + '/TransOutTry', svc + '/TransOutConfirm', svc + '/TransOutCancel')
    # 调用转入服务的Try|Confirm|Cancel
    t.call_branch(req, svc + '/TransInTry', svc + '/TransInConfirm', svc + '/TransInCancel')


@app.get('/api/fireSaga')
def fire_saga() -> Dict[str, str]:
    print('调用fire_saga()函数，调用路径：/api/fireSaga，调用方式：get')
    req: Dict[str, int] = {'amount': 30}
    s: saga.Saga = saga.Saga(dtm, utils.gen_gid(dtm))
    s.add(req, svc + '/TransOutSaga', svc + '/TransOutCompensate')
    s.add(req, svc + '/TransInSaga', svc + '/TransInCompensate')
    s.submit()
    return {'gid': s.trans_base.gid}


@app.get('/api/fireMsg')
def fire_msg() -> Dict[str, str]:
    print('调用fire_msg()函数，调用路径：/api/fireMsg，调用方式：get')
    req: Dict[str, int] = {'amount': 30}
    m: msg.Msg = msg.Msg(dtm, utils.gen_gid(dtm))
    m.add(req, svc + '/TransOutSaga')
    m.add(req, svc + 'TransInSaga')
    m.submit()
    return {'gid': m.trans_base.gid}


@app.get('/api/fireMsgdb')
def fire_msgdb() -> Dict[str, str]:
    print('调用fire_msgdb()函数，调用路径：/api/fireMsgdb，调用方式：get')
    req: Dict[str, int] = {'amount': 30}
    m: msg.Msg = msg.Msg(dtm, utils.gen_gid(dtm))
    m.add(req, svc + 'TransSaga')

    def busi_callback(c):
        saga_adjust_balance(c, out_uid, -30)

    with barrier.AutoCursor(conn_new()) as cursor:
        m.do_and_submit_db(svc + '/queryprepared', cursor, busi_callback)
    return {'gid': m.trans_base.gid}


@app.get('/api/queryprepared')
def query_prepared() -> Union[Dict[str, str], Tuple[Dict[str, str], int]]:
    print('调用query_prepared()函数，调用路径：/api/queryprepared，调用方式：get')
    with barrier.AutoCursor(conn_new()) as cursor:
        try:
            barrier_from_req(request).query_prepared(cursor)
        except utils.DTMFailureError as e:
            return {'dtm_result': 'DTMFAILUREERROR'}, 409
        except Exception as e:
            return {'dtm_result': 'UNKNOWN'}, 500
    return {'dtm_result': 'SUCCESS'}


def tcc_adjust_trading(cursor, uid: int, amount: int) -> None:
    print('调用tcc_adjust_trading()函数')
    affected: Any = utils.sqlexec(
        cursor,
        "update dtm_busi.user_account set trading_balance=trading_balance+%d	where user_id=%d and trading_balance + %d + balance >= 0" % (
            amount, uid, amount)
    )
    if affected == 0:
        raise Exception('update error, maybe balance not enough')


def tcc_adjust_balance(cursor, uid: int, amount: int) -> None:
    print('调用tcc_adjust_balance()函数')
    utils.sqlexec(
        cursor,
        "update dtm_busi.user_account set trading_balance=trading_balance-%d, balance=balance+%d where user_id=%d" % (
            amount, amount, uid)
    )


def saga_adjust_balance(cursor, uid: int, amount: int) -> None:
    print('调用saga_adjust_balance()函数')
    affected: Any = utils.sqlexec(
        cursor,
        "update dtm_busi.user_account set balance=balance+%d where user_id=%d and balance >= -%d" % (
            amount, uid, amount)
    )
    if affected == 0:
        raise Exception("update error, balance not enough")


@app.post('/api/TransOutTry')
def trans_out_try() -> Dict[str, str]:
    print('调用trans_out_try()函数，调用路径：/api/TransOutTry，调用方式：post')
    with barrier.AutoCursor(conn_new()) as cursor:
        def busi_callback(c):
            print('调用busi_callback()函数，上层调用函数：trans_out_try')
            tcc_adjust_trading(c, out_uid, -30)

        barrier_from_req(request).call(cursor, busi_callback)
    return {'dtm_result': 'SUCCESS'}


@app.post('/api/TransOutConfirm')
def trans_out_confirm():
    print('调用trans_out_confirm()函数，调用路径：/api/TransOutConfirm，调用方式：post')
    with barrier.AutoCursor(conn_new()) as cursor:
        def busi_callback(c):
            print('调用busi_callback()函数，上层调用函数：trans_out_confirm')
            tcc_adjust_balance(c, out_uid, -30)

        barrier_from_req(request).call(cursor, busi_callback)
    return {'dtm_result': 'SUCCESS'}


@app.post('/api/TransOutCancel')
def trans_out_cancel():
    print('调用trans_out_cancel()函数，调用路径：/api/TransOutCancel，调用方式：post')
    with barrier.AutoCursor(conn_new()) as cursor:
        def busi_callback(c):
            print('调用busi_callback()函数，上层调用函数：trans_out_cancel')
            tcc_adjust_trading(c, out_uid, 30)

        barrier_from_req(request).call(cursor, busi_callback)
    return {'dtm_result': 'SUCCESS'}


@app.post('/api/TransInTry')
def trans_in_try():
    print('调用trans_in_try()函数，调用路径：/api/TransInTry，调用方式：post')
    with barrier.AutoCursor(conn_new()) as cursor:
        def busi_callback(c):
            print('调用busi_callback()函数，上层调用函数：trans_in_try')
            tcc_adjust_trading(c, in_uid, 30)

        barrier_from_req(request).call(cursor, busi_callback)
    return {'dtm_result': 'SUCCESS'}


@app.post('/api/TransInConfirm')
def trans_in_confirm():
    print('调用trans_in_confirm()函数，调用路径：/api/TransInConfirm，调用方式：post')
    with barrier.AutoCursor(conn_new()) as cursor:
        def busi_callback(c):
            print('调用busi_callback()函数，上层调用函数：trans_in_confirm')
            tcc_adjust_balance(c, in_uid, 30)

        barrier_from_req(request).call(cursor, busi_callback)
    return {'dtm_result': 'SUCCESS'}


@app.post('/api/TransInCancel')
def trans_in_cancel():
    print('调用trans_in_cancel()函数，调用路径：/api/TransInCancel，调用方式：post')
    with barrier.AutoCursor(conn_new()) as cursor:
        def busi_callback(c):
            print('调用busi_callback()函数，上层调用函数：trans_in_cancel')
            tcc_adjust_trading(c, in_uid, -30)

        barrier_from_req(request).call(cursor, busi_callback)
    return {"dtm_result": "SUCCESS"}


@app.post('/api/TransOutSaga')
def trans_out_saga():
    print('调用trans_out_saga()函数，调用路径：/api/TransOutSaga，调用方式：post')
    with barrier.AutoCursor(conn_new()) as cursor:
        def busi_callback(c):
            print('调用busi_callback()函数，上层调用函数：trans_out_saga')
            saga_adjust_balance(c, out_uid, -30)

        barrier_from_req(request).call(cursor, busi_callback)
    return {'dtm_result': 'SUCCESS'}


@app.post('/api/TransOutCompensate')
def trans_out_compensate():
    print('调用trans_out_compensate()函数，调用路径：/api/TransOutCompensate，调用方式：post')
    with barrier.AutoCursor(conn_new()) as cursor:
        def busi_callback(c):
            print('调用busi_callback()函数，上层调用函数：trans_out_compensate')
            saga_adjust_balance(c, out_uid, 30)

        barrier_from_req(request).call(cursor, busi_callback)
    return {"dtm_result": "SUCCESS"}


@app.post('/api/TransInSaga')
def trans_in_saga():
    print('调用trans_in_saga()函数，调用路径：/api/TransInSaga，调用方式：post')
    with barrier.AutoCursor(conn_new()) as cursor:
        def busi_callback(c):
            print('调用busi_callback()函数，上层调用函数：trans_in_saga')
            saga_adjust_balance(c, in_uid, 30)

        barrier_from_req(request).call(cursor, busi_callback)
    return {"dtm_result": "SUCCESS"}


@app.post('/api/TransInCompensate')
def trans_in_compensate():
    print('调用trans_in_compensate()函数，调用路径：/api/TransInCompensate，调用方式：post')
    with barrier.AutoCursor(conn_new()) as cursor:
        def busi_callback(c):
            print('调用busi_callback()函数，上层调用函数：trans_in_compensate')
            saga_adjust_balance(c, in_uid, -30)

        barrier_from_req(request).call(cursor, busi_callback)
    return {"dtm_result": "SUCCESS"}


if __name__ == '__main__':
    app.run()
