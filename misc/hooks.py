"""
Internal module (hooks)

This is an internal module that contains implementations of all the hooks
that are used. Some of the things that are hooked are things such as
comment creation, function and segment scoping, etc. This is not intended
to be used by the average user.
"""

import six
import sys, logging
import functools, operator, itertools, types

import database, function, ui
import internal
from internal import comment, utils, exceptions as E

import idaapi

### general hooks
def noapi(*args):
    fr = sys._getframe().f_back
    if fr is None:
        logging.fatal(u"{:s}.noapi() : Unexpected empty frame ({!s}) from caller. Continuing from {!s}..".format(__name__, utils.string.repr(sys._getframe()), utils.string.repr(sys._getframe().f_code)))
        return hook.CONTINUE

    return internal.interface.priorityhook.CONTINUE if fr.f_back is None else internal.interface.priorityhook.STOP

def notify(name):
    def notification(*args):
        logging.warn(u"{:s}.notify({!s}) : Received notification for {!s} with args ({!s}).".format(__name__, utils.string.repr(name), utils.string.repr(name), utils.string.repr(args)))
    notification.__name__ = "notify({:s})".format(name)
    return notification

### comment hooks
class comment(object):
    @classmethod
    def database_init(cls, idp_modname):
        if hasattr(cls, 'event'):
            return
        cls.event = cls._event()
        next(cls.event)

class address(comment):
    @classmethod
    def _is_repeatable(cls, ea):
        f = idaapi.get_func(ea)
        return True if f is None else False

    @classmethod
    def _update_refs(cls, ea, old, new):
        f = idaapi.get_func(ea)
        for key in old.viewkeys() ^ new.viewkeys():
            if key not in new:
                logging.debug(u"{:s}.update_refs({:#x}) : Decreasing refcount for {!s} at {:s}. Updating old keys ({!s}) to new keys ({!s}).".format('.'.join((__name__, cls.__name__)), ea, utils.string.repr(key), 'address', utils.string.repr(old.viewkeys()), utils.string.repr(new.viewkeys())))
                if f: internal.comment.contents.dec(ea, key)
                else: internal.comment.globals.dec(ea, key)
            if key not in old:
                logging.debug(u"{:s}.update_refs({:#x}) : Increasing refcount for {!s} at {:s}. Updating old keys ({!s}) to new keys ({!s}).".format('.'.join((__name__, cls.__name__)), ea, utils.string.repr(key), 'address', utils.string.repr(old.viewkeys()), utils.string.repr(new.viewkeys())))
                if f: internal.comment.contents.inc(ea, key)
                else: internal.comment.globals.inc(ea, key)
            continue
        return

    @classmethod
    def _create_refs(cls, ea, res):
        f = idaapi.get_func(ea)
        for key in res.viewkeys():
            logging.debug(u"{:s}.create_refs({:#x}) : Increasing refcount for {!s} at {:s} for keys ({!s}).".format('.'.join((__name__, cls.__name__)), ea, utils.string.repr(key), 'address', utils.string.repr(res.viewkeys())))
            if f: internal.comment.contents.inc(ea, key)
            else: internal.comment.globals.inc(ea, key)
        return

    @classmethod
    def _delete_refs(cls, ea, res):
        f = idaapi.get_func(ea)
        for key in res.viewkeys():
            logging.debug(u"{:s}.delete_refs({:#x}) : Decreasing refcount for {!s} at {:s} for keys ({!s}).".format('.'.join((__name__, cls.__name__)), ea,  utils.string.repr(key), 'address', utils.string.repr(res.viewkeys())))
            if f: internal.comment.contents.dec(ea, key)
            else: internal.comment.globals.dec(ea, key)
        return

    @classmethod
    def _event(cls):
        while True:
            # cmt_changing event
            ea, rpt, new = (yield)
            old = utils.string.of(idaapi.get_cmt(ea, rpt))
            f, o, n = idaapi.get_func(ea), internal.comment.decode(old), internal.comment.decode(new)

            # update references before we update the comment
            cls._update_refs(ea, o, n)

            # wait for cmt_changed event
            newea, nrpt, none = (yield)

            # now fix the comment the user typed
            if (newea, nrpt, none) == (ea, rpt, None):
                ncmt, repeatable = utils.string.of(idaapi.get_cmt(ea, rpt)), cls._is_repeatable(ea)

                if (ncmt or '') != new:
                    logging.warn(u"{:s}.event() : Comment from event at address {:#x} is different from database. Expected comment ({!s}) is different from current comment ({!s}).".format('.'.join((__name__, cls.__name__)), ea, utils.string.repr(new), utils.string.repr(ncmt)))

                # delete it if it's the wrong type
#                if nrpt != repeatable:
#                    idaapi.set_cmt(ea, '', nrpt)

#                # write the tag back to the address
#                if internal.comment.check(new): idaapi.set_cmt(ea, utils.string.to(internal.comment.encode(n)), repeatable)
#                # write the comment back if it's non-empty
#                elif new: idaapi.set_cmt(ea, utils.string.to(new), repeatable)
#                # otherwise, remove its reference since it's being deleted
#                else: cls._delete_refs(ea, n)

                if internal.comment.check(new): idaapi.set_cmt(ea, utils.string.to(internal.comment.encode(n)), rpt)
                elif new: idaapi.set_cmt(ea, utils.string.to(new), rpt)
                else: cls._delete_refs(ea, n)

                continue

            # if the changed event doesn't happen in the right order
            logging.fatal(u"{:s}.event() : Comment events are out of sync at address {:#x}, updating tags from previous comment. Expected comment ({!s}) is different from current comment ({!s}).".format('.'.join((__name__, cls.__name__)), ea, utils.string.repr(o), utils.string.repr(n)))

            # delete the old comment
            cls._delete_refs(ea, o)
            idaapi.set_cmt(ea, '', rpt)
            logging.warn(u"{:s}.event() : Deleted comment at address {:#x} was {!s}.".format('.'.join((__name__, cls.__name__)), ea, utils.string.repr(o)))

            # new comment
            new = utils.string.of(idaapi.get_cmt(newea, nrpt))
            n = internal.comment.decode(new)
            cls._create_refs(newea, n)

            continue
        return

    @classmethod
    def changing(cls, ea, repeatable_cmt, newcmt):
        logging.debug(u"{:s}.changing({:#x}, {:d}, {!s}) : Received comment.changing event for a {:s} comment at {:x}.".format('.'.join((__name__, cls.__name__)), ea, repeatable_cmt, utils.string.repr(newcmt), 'repeatable' if repeatable_cmt else 'non-repeatable', ea))
        oldcmt = utils.string.of(idaapi.get_cmt(ea, repeatable_cmt))
        try: cls.event.send((ea, bool(repeatable_cmt), utils.string.of(newcmt)))
        except StopIteration, e:
            logging.fatal(u"{:s}.changing({:#x}, {:d}, {!s}) : Unexpected termination of event handler. Re-instantiating it.".format('.'.join((__name__, cls.__name__)), ea, repeatable_cmt, utils.string.repr(newcmt)))
            cls.event = cls._event(); next(cls.event)

    @classmethod
    def changed(cls, ea, repeatable_cmt):
        logging.debug(u"{:s}.changed({:#x}, {:d}) : Received comment.changed event for a {:s} comment at {:x}.".format('.'.join((__name__, cls.__name__)), ea, repeatable_cmt, 'repeatable' if repeatable_cmt else 'non-repeatable', ea))
        newcmt = utils.string.of(idaapi.get_cmt(ea, repeatable_cmt))
        try: cls.event.send((ea, bool(repeatable_cmt), None))
        except StopIteration, e:
            logging.fatal(u"{:s}.changed({:#x}, {:d}) : Unexpected termination of event handler. Re-instantiating it.".format('.'.join((__name__, cls.__name__)), ea, repeatable_cmt))
            cls.event = cls._event(); next(cls.event)

class globals(comment):
    @classmethod
    def _update_refs(cls, fn, old, new):
        for key in old.viewkeys() ^ new.viewkeys():
            if key not in new:
                logging.debug(u"{:s}.update_refs({:#x}) : Decreasing refcount for {!s} at {:s}. Updating old keys ({!s}) to new keys ({!s}).".format('.'.join((__name__, cls.__name__)), fn.startEA if fn else idaapi.BADADDR, utils.string.repr(key), 'function' if fn else 'global', utils.string.repr(old.viewkeys()), utils.string.repr(new.viewkeys())))
                internal.comment.globals.dec(fn.startEA, key)
            if key not in old:
                logging.debug(u"{:s}.update_refs({:#x}) : Increasing refcount for {!s} at {:s}. Updating old keys ({!s}) to new keys ({!s}).".format('.'.join((__name__, cls.__name__)), fn.startEA if fn else idaapi.BADADDR, utils.string.repr(key), 'function' if fn else 'global', utils.string.repr(old.viewkeys()), utils.string.repr(new.viewkeys())))
                internal.comment.globals.inc(fn.startEA, key)
            continue
        return

    @classmethod
    def _create_refs(cls, fn, res):
        for key in res.viewkeys():
            internal.comment.globals.inc(fn.startEA, key)
            logging.debug(u"{:s}.create_refs({:#x}) : Increasing refcount for {!s} at {:s} for keys ({!s}).".format('.'.join((__name__, cls.__name__)), fn.startEA if fn else idaapi.BADADDR, utils.string.repr(key), 'function' if fn else 'global', utils.string.repr(res.viewkeys())))
        return

    @classmethod
    def _delete_refs(cls, fn, res):
        for key in res.viewkeys():
            internal.comment.globals.dec(fn.startEA, key)
            logging.debug(u"{:s}.delete_refs({:#x}) : Decreasing refcount for {!s} at {:s} for keys ({!s}).".format('.'.join((__name__, cls.__name__)), fn.startEA if fn else idaapi.BADADDR, utils.string.repr(key), 'function' if fn else 'global', utils.string.repr(res.viewkeys())))
        return

    @classmethod
    def _event(cls):
        while True:
            # cmt_changing event
            ea, rpt, new = (yield)
            fn = idaapi.get_func(ea)
            old = utils.string.of(idaapi.get_func_cmt(fn, rpt))
            o, n = internal.comment.decode(old), internal.comment.decode(new)

            # update references before we update the comment
            cls._update_refs(fn, o, n)

            # wait for cmt_changed event
            newea, nrpt, none = (yield)

            # now we can fix the user's new coment
            if (newea, nrpt, none) == (ea, rpt, None):
                ncmt = utils.string.of(idaapi.get_func_cmt(fn, rpt))

                if (ncmt or '') != new:
                    logging.warn(u"{:s}.event() : Comment from event for function {:#x} is different from database. Expected comment ({!s}) is different from current comment ({!s}).".format('.'.join((__name__, cls.__name__)), ea, utils.string.repr(new), utils.string.repr(ncmt)))

                # if it's non-repeatable, then fix it.
#                if not nrpt:
#                    idaapi.set_func_cmt(fn, '', nrpt)

#                # write the tag back to the function
#                if internal.comment.check(new): idaapi.set_func_cmt(fn, utils.string.to(internal.comment.encode(n)), True)
#                # otherwise, write the comment back as long as it's valid
#                elif new: idaapi.set_func_cmt(fn, utils.string.to(new), True)
#                # otherwise, the user has deleted it..so update its refs.
#                else: cls._delete_refs(fn, n)

                # write the tag back to the function
                if internal.comment.check(new): idaapi.set_func_cmt(fn, utils.string.to(internal.comment.encode(n)), rpt)
                elif new: idaapi.set_func_cmt(fn, utils.string.to(new), rpt)
                else: cls._delete_refs(fn, n)
                continue

            # if the changed event doesn't happen in the right order
            logging.fatal(u"{:s}.event() : Comment events are out of sync for function {:#x}, updating tags from previous comment. Expected comment ({!s}) is different from current comment ({!s}).".format('.'.join((__name__, cls.__name__)), ea, utils.string.repr(o), utils.string.repr(n)))

            # delete the old comment
            cls._delete_refs(fn, o)
            idaapi.set_func_cmt(fn, '', rpt)
            logging.warn(u"{:s}.event() : Deleted comment for function {:#x} was ({!s}).".format('.'.join((__name__, cls.__name__)), ea, utils.string.repr(o)))

            # new comment
            newfn = idaapi.get_func(newea)
            new = utils.string.of(idaapi.get_func_cmt(newfn, nrpt))
            n = internal.comment.decode(new)
            cls._create_refs(newfn, n)

            continue
        return

    @classmethod
    def changing(cls, cb, a, cmt, repeatable):
        logging.debug(u"{:s}.changing({!s}, {:#x}, {!s}, {:d}) : Received comment.changing event for a {:s} comment at {:x}.".format('.'.join((__name__, cls.__name__)), utils.string.repr(cb), a.startEA, utils.string.repr(cmt), repeatable, 'repeatable' if repeatable else 'non-repeatable', a.startEA))
        fn = idaapi.get_func(a.startEA)
        if fn is None and not cmt: return
        oldcmt = utils.string.of(idaapi.get_func_cmt(fn, repeatable))
        try: cls.event.send((fn.startEA, bool(repeatable), utils.string.of(cmt)))
        except StopIteration, e:
            logging.fatal(u"{:s}.changing({!s}, {:#x}, {!s}, {:d}) : Unexpected termination of event handler. Re-instantiating it.".format('.'.join((__name__, cls.__name__)), utils.string.repr(cb), a.startEA, utils.string.repr(cmt), repeatable))
            cls.event = cls._event(); next(cls.event)

    @classmethod
    def changed(cls, cb, a, cmt, repeatable):
        logging.debug(u"{:s}.changed({!s}, {:#x}, {!s}, {:d}) : Received comment.changed event for a {:s} comment at {:x}.".format('.'.join((__name__, cls.__name__)), utils.string.repr(cb), a.startEA, utils.string.repr(cmt), repeatable, 'repeatable' if repeatable else 'non-repeatable', a.startEA))
        fn = idaapi.get_func(a.startEA)
        if fn is None and not cmt: return
        newcmt = utils.string.of(idaapi.get_func_cmt(fn, repeatable))
        try: cls.event.send((fn.startEA, bool(repeatable), None))
        except StopIteration, e:
            logging.fatal(u"{:s}.changed({!s}, {:#x}, {!s}, {:d}) : Unexpected termination of event handler. Re-instantiating it.".format('.'.join((__name__, cls.__name__)), utils.string.repr(cb), a.startEA, utils.string.repr(cmt), repeatable))
            cls.event = cls._event(); next(cls.event)

### database scope
class state(object):
    # database notification state
    init = type('init', (object,), {})()
    loaded = type('loaded', (object,), {})()
    ready = type('ready', (object,), {})()

State = None

def on_init(idp_modname):
    '''IDP_Hooks.init'''

    # Database has just been opened, setup the initial state.
    global State
    if State == None:
        State = state.init
    else:
        logging.debug(u"{:s}.on_init({!s}) : Received unexpected state transition from state ({!s}).".format(__name__, utils.string.repr(idp_modname), utils.string.repr(State)))

def on_newfile(fname):
    '''IDP_Hooks.newfile'''

    # Database has been created, switch the state to loaded.
    global State
    if State == state.init:
        State = state.loaded
    else:
        logging.debug(u"{:s}.on_newfile({!s}) : Received unexpected state transition from state ({!s}).".format(__name__, utils.string.repr(fname), utils.string.repr(State)))
    # FIXME: save current state like base addresses and such

def on_oldfile(fname):
    '''IDP_Hooks.oldfile'''

    # Database has been loaded, switch the state to ready.
    global State
    if State == state.init:
        State = state.ready

        __check_functions()
    else:
        logging.debug(u"{:s}.on_oldfile({!s}) : Received unexpected state transition from state ({!s}).".format(__name__, utils.string.repr(fname), utils.string.repr(State)))
    # FIXME: save current state like base addresses and such

def __check_functions():
    # FIXME: check if tagcache needs to be created
    return

def on_ready():
    '''IDP_Hooks.auto_empty'''
    global State

    # Queues have just been emptied, so now we can transition
    if State == state.loaded:
        State = state.ready

        # update tagcache using function state
        __process_functions()

    elif State == state.ready:
        logging.debug(u"{:s}.on_ready() : Database is already ready ({!s}).".format(__name__, utils.string.repr(State)))

    else:
        logging.debug(u"{:s}.on_ready() : Received unexpected transition from state ({!s}).".format(__name__, utils.string.repr(State)))

def auto_queue_empty(type):
    if type == idaapi.AU_FINAL:
        on_ready()

def __process_functions(percentage=0.10):
    p = ui.Progress()
    globals = set(internal.comment.globals.address())

    total = 0

    funcs = list(database.functions())
    p.update(current=0, max=len(funcs), title=u"Pre-building tagcache...")
    p.open()
    six.print_(u"Pre-building tagcache for {:d} functions.".format(len(funcs)))
    for i, fn in enumerate(funcs):
        chunks = list(function.chunks(fn))

        text = functools.partial(u"Processing function {:#x} ({chunks:d} chunk{plural:s}) -> {:d} of {:d}".format, fn, i + 1, len(funcs))
        p.update(current=i)
        ui.navigation.procedure(fn)
        if i % (int(len(funcs) * percentage) or 1) == 0:
            six.print_(u"Processing function {:#x} -> {:d} of {:d} ({:.02f}%)".format(fn, i+1, len(funcs), i / float(len(funcs)) * 100.0))

        contents = set(internal.comment.contents.address(fn))
        for ci, (l, r) in enumerate(chunks):
            p.update(text=text(chunks=len(chunks), plural='' if len(chunks) == 1 else 's'), tooltip="Chunk #{:d} : {:#x} - {:#x}".format(ci, l, r))
            ui.navigation.analyze(l)
            for ea in database.address.iterate(l, r):
                # FIXME: no need to iterate really since we should have
                #        all of the addresses
                for k, v in six.iteritems(database.tag(ea)):
                    if ea in globals: internal.comment.globals.dec(ea, k)
                    if ea not in contents: internal.comment.contents.inc(ea, k, target=fn)
                    total += 1
                continue
            continue
        continue
    six.print_(u"Successfully built tag-cache composed of {:d} tag{:s}.".format(total, '' if total == 1 else 's'))
    p.close()

def rebase(info):
    functions, globals = map(utils.fcompose(sorted, list), (database.functions(), internal.netnode.alt.fiter(internal.comment.tagging.node())))

    p = ui.Progress()
    p.update(current=0, title=u"Rebasing tagcache...", min=0, max=len(functions)+len(globals))
    fcount = gcount = 0

    scount = info.size() + 1
    six.print_(u"{:s}.rebase({!s}) : Rebasing tagcache for {:d} segments.".format(__name__, utils.string.repr(info), scount))

    # for each segment
    p.open()
    for si in six.moves.range(scount):
        msg = u"Rebasing tagcache for segment {:d} of {:d} : {:#x} ({:+#x}) -> {:#x}".format(si, scount, info[si]._from, info[si].size, info[si].to)
        p.update(title=msg), six.print_(msg)

        # for each function (using target address because ida moved the netnodes for us)
        res = [n for n in functions if info[si].to <= n < info[si].to + info[si].size]
        for i, fn in __rebase_function(info[si]._from, info[si].to, info[si].size, iter(res)):
            text = u"Function {:d} of {:d} : {:#x}".format(i + fcount, len(functions), fn)
            p.update(value=sum((fcount, gcount, i)), text=text)
            ui.navigation.procedure(fn)
        fcount += len(res)

        # for each global
        res = [(ea, count) for ea, count in globals if info[si]._from <= ea < info[si]._from + info[si].size]
        for i, ea in __rebase_globals(info[si]._from, info[si].to, info[si].size, iter(res)):
            text = u"Global {:d} of {:d} : {:#x}".format(i + gcount, len(globals), ea)
            p.update(value=sum((fcount, gcount, i)), text=text)
            ui.navigation.analyze(ea)
        gcount += len(res)
    p.close()

def __rebase_function(old, new, size, iterable):
    key = internal.comment.tagging.__address__
    failure, total = [], list(iterable)

    for i, fn in enumerate(total):
        # grab the contents dictionary
        try:
            state = internal.comment.contents._read(None, fn)
        except E.FunctionNotFoundError:
            logging.fatal(u"{:s}.rebase({:#x}, {:#x}, {:-#x}, {!r}) : Address {:#x} -> {:#x} is not a function.".format(__name__, old, new, size, iterable, fn - new + old, fn))
            state = None
        if state is None: continue

        # now we can erase the old one
        res = fn - new + old
        internal.comment.contents._write(res, None, None)

        # update the addresses
        res, state[key] = state[key], {ea - old + new : ref for ea, ref in six.iteritems(state[key])}

        # and put the new addresses back
        ok = internal.comment.contents._write(None, fn, state)
        if not ok:
            logging.fatal(u"{:s}.rebase({:#x}, {:#x}, {:-#x}, {!r}) : Failure trying to write refcount for function {:#x} while trying to update old reference count ({!s}) to new one ({!s}).".format(__name__, old, new, size, iterable, fn, utils.string.repr(res), utils.string.repr(state[key])))
            failure.append((fn, res, state[key]))

        yield i, fn
    return

def __rebase_globals(old, new, size, iterable):
    node = internal.comment.tagging.node()
    failure, total = [], list(iterable)
    for i, (ea, count) in enumerate(total):
        # remove the old address
        ok = internal.netnode.alt.remove(node, ea)
        if not ok:
            logging.fatal(u"{:s}.rebase({:#x}, {:#x}, {:-#x}, {!r}) : Failure trying to remove refcount ({!r}) for global {:#x}.".format(__name__, old, new, size, iterable, count, ea))

        # now add the new address
        res = ea - old + new
        ok = internal.netnode.alt.set(node, res, count)
        if not ok:
            logging.fatal(u"{:s}.rebase({:#x}, {:#x}, {:-#x}, {!r}) : Failure trying to store refcount ({!r}) from {:#x} to {:#x}.".format(__name__, old, new, size, iterable, count, ea, res))

            failure.append((ea, res, count))
        yield i, ea
    return

# address naming
def rename(ea, newname):
    fl = database.type.flags(ea)
    labelQ, customQ = (fl & n == n for n in {idaapi.FF_LABL, idaapi.FF_NAME})
    #r, fn = database.xref.up(ea), idaapi.get_func(ea)
    fn = idaapi.get_func(ea)

    # figure out whether a global or function name is being changed, otherwise it's the function's contents
    ctx = internal.comment.globals if not fn or (fn.startEA == ea) else internal.comment.contents

    # if a name is being removed
    if not newname:
        # if it's a custom name
        if (not labelQ and customQ):
            ctx.dec(ea, '__name__')
            logging.debug(u"{:s}.rename({:#x}, {!s}) : Decreasing refcount for tag {!r} at address due to an empty name.".format(__name__, ea, utils.string.repr(newname), '__name__'))
        return

    # if it's currently a label or is unnamed
    if (labelQ and not customQ) or all(not q for q in {labelQ, customQ}):
        ctx.inc(ea, '__name__')
        logging.debug(u"{:s}.rename({:#x}, {!s}) : Increasing refcount for tag {!r} at address due to a new name.".format(__name__, ea, utils.string.repr(newname), '__name__'))
    return

def extra_cmt_changed(ea, line_idx, cmt):
    # FIXME: persist state for extra_cmts in order to determine
    #        what the original value was before modification
    # XXX: IDA doesn't seem to have an extra_cmt_changing event and instead calls this hook twice for every insertion

    oldcmt = internal.netnode.sup.get(ea, line_idx)
    if oldcmt is not None: oldcmt = oldcmt.rstrip('\x00')
    ctx = internal.comment.contents if idaapi.get_func(ea) else internal.comment.globals

    MAX_ITEM_LINES = (idaapi.E_NEXT-idaapi.E_PREV) if idaapi.E_NEXT > idaapi.E_PREV else idaapi.E_PREV-idaapi.E_NEXT
    prefix = (idaapi.E_PREV, idaapi.E_PREV+MAX_ITEM_LINES, '__extra_prefix__')
    suffix = (idaapi.E_NEXT, idaapi.E_NEXT+MAX_ITEM_LINES, '__extra_suffix__')

    for l, r, key in (prefix, suffix):
        if l <= line_idx < r:
            if oldcmt is None and cmt is not None: ctx.inc(ea, key)
            elif oldcmt is not None and cmt is None: ctx.dec(ea, key)
            logging.debug(u"{:s}.extra_cmt_changed({:#x}, {:d}, {!s}, oldcmt={!s}) : {:s} refcount at address for tag {!s}.".format(__name__, ea, line_idx, utils.string.repr(cmt), utils.string.repr(oldcmt), 'Increasing' if oldcmt is None and cmt is not None else 'Decreasing' if oldcmt is not None and cmt is None else 'Doing nothing to', utils.string.repr(key)))
        continue
    return

### function scope
def thunk_func_created(pfn):
    pass

def func_tail_appended(pfn, tail):
    global State
    if State != state.ready: return
    # tail = func_t
    for ea in database.address.iterate(tail.startEA, tail.endEA):
        for k in database.tag(ea):
            internal.comment.globals.dec(ea, k)
            internal.comment.contents.inc(ea, k, target=pfn.startEA)
            logging.debug(u"{:s}.func_tail_appended({:#x}, {:#x}) : Exchanging (decreasing) refcount for global tag {!s} and (increasing) refcount for contents tag {!s}.".format(__name__, pfn.startEA, tail.startEA, utils.string.repr(k), utils.string.repr(k)))
        continue
    return

def removing_func_tail(pfn, tail):
    global State
    if State != state.ready: return
    # tail = area_t
    for ea in database.address.iterate(tail.startEA, tail.endEA):
        for k in database.tag(ea):
            internal.comment.contents.dec(ea, k, target=pfn.startEA)
            internal.comment.globals.inc(ea, k)
            logging.debug(u"{:s}.removing_func_tail({:#x}, {:#x}) : Exchanging (increasing) refcount for global tag {!s} and (decreasing) refcount for contents tag {!s}.".format(__name__, pfn.startEA, tail.startEA, utils.string.repr(k), utils.string.repr(k)))
        continue
    return

def add_func(pfn):
    global State
    if State != state.ready: return
    # convert all globals into contents
    for l, r in function.chunks(pfn):
        for ea in database.address.iterate(l, r):
            for k in database.tag(ea):
                internal.comment.globals.dec(ea, k)
                internal.comment.contents.inc(ea, k, target=pfn.startEA)
                logging.debug(u"{:s}.add_func({:#x}) : Exchanging (decreasing) refcount for global tag {!s} and (increasing) refcount for contents tag {!s}.".format(__name__, pfn.startEA, utils.string.repr(k), utils.string.repr(k)))
            continue
        continue
    return

def del_func(pfn):
    global State
    if State != state.ready: return
    # convert all contents into globals
    for l, r in function.chunks(pfn):
        for ea in database.address.iterate(l, r):
            for k in database.tag(ea):
                internal.comment.contents.dec(ea, k, target=pfn.startEA)
                internal.comment.globals.inc(ea, k)
                logging.debug(u"{:s}.del_func({:#x}) : Exchanging (increasing) refcount for global tag {!s} and (decreasing) refcount for contents tag {!s}.".format(__name__, pfn.startEA, utils.string.repr(k), utils.string.repr(k)))
            continue
        continue

    # remove all function tags
    for k in function.tag(pfn.startEA):
        internal.comment.globals.dec(pfn.startEA, k)
        logging.debug(u"{:s}.del_func({:#x}) : Removing (global) tag {!s} from function.".format(__name__, pfn.startEA, utils.string.repr(k)))
    return

def set_func_start(pfn, new_start):
    global State
    if State != state.ready: return
    # new_start has removed addresses from function
    # replace contents with globals
    if pfn.startEA > new_start:
        for ea in database.address.iterate(new_start, pfn.startEA):
            for k in database.tag(ea):
                internal.comment.contents.dec(ea, k, target=pfn.startEA)
                internal.comment.globals.inc(ea, k)
                logging.debug(u"{:s}.set_func_start({:#x}, {:#x}) : Exchanging (increasing) refcount for global tag {!s} and (decreasing) refcount for contents tag {!s}.".format(__name__, pfn.startEA, new_start, utils.string.repr(k), utils.string.repr(k)))
            continue
        return

    # new_start has added addresses to function
    # replace globals with contents
    elif pfn.startEA < new_start:
        for ea in database.address.iterate(pfn.startEA, new_start):
            for k in database.tag(ea):
                internal.comment.globals.dec(ea, k)
                internal.comment.contents.inc(ea, k, target=pfn.startEA)
                logging.debug(u"{:s}.set_func_start({:#x}, {:#x}) : Exchanging (decreasing) refcount for global tag {!s} and (increasing) refcount for contents tag {!s}.".format(__name__, pfn.startEA, new_start, utils.string.repr(k), utils.string.repr(k)))
            continue
        return
    return

def set_func_end(pfn, new_end):
    global State
    if State != state.ready: return
    # new_end has added addresses to function
    # replace globals with contents
    if new_end > pfn.endEA:
        for ea in database.address.iterate(pfn.endEA, new_end):
            for k in database.tag(ea):
                internal.comment.globals.dec(ea, k)
                internal.comment.contents.inc(ea, k, target=pfn.startEA)
                logging.debug(u"{:s}.set_func_end({:#x}, {:#x}) : Exchanging (decreasing) refcount for global tag {!s} and (increasing) refcount for contents tag {!s}.".format(__name__, pfn.startEA, new_end, utils.string.repr(k), utils.string.repr(k)))
            continue
        return

    # new_end has removed addresses from function
    # replace contents with globals
    elif new_end < pfn.endEA:
        for ea in database.address.iterate(new_end, pfn.endEA):
            for k in database.tag(ea):
                internal.comment.contents.dec(ea, k, target=pfn.startEA)
                internal.comment.globals.inc(ea, k)
                logging.debug(u"{:s}.set_func_end({:#x}, {:#x}) : Exchanging (increasing) refcount for global tag {!s} and (decreasing) refcount for contents tag {!s}.".format(__name__, pfn.startEA, new_end, utils.string.repr(k), utils.string.repr(k)))
            continue
        return
    return
