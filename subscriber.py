#!/usr/bin/env python
# coding: utf-8

from boto.sqs.connection import SQSConnection
from boto.sqs.regioninfo import SQSRegionInfo
from multiprocessing import Process, Queue, active_children
from Queue import Empty
from boto.sqs import connect_to_region
from socket import gethostname
import simplejson as json
import subprocess, time
import signal
import time
import sys
import yaml
import os
import stat
import getopt
import logging


CFILE='/home/adh/ec2_collective/agent.json'

def terminate_process(signum, frame):
    logging.debug ('Process asked to exit...')
    sys.exit(1)

signal.signal(signal.SIGTERM, terminate_process)

def usage():
    print >>sys.stderr, '    Usage:'
    print >>sys.stderr, '    ' + sys.argv[0] + '\n\n -f, --foreground\trun script in foreground\n -h, --help\tthis help\n -l, --logfile\tlogfile path ( /var/log/ec2_collective.log )\n -p, --pidfile\tpath to pidfile (/var/run/ec2_collectived.pid)'
    sys.exit(1)

def set_logging():

    logformat = '%(asctime)s [%(levelname)s] %(message)s'
    logging.basicConfig(level=logging.INFO, format=logformat)
    logging.getLogger('boto').setLevel(logging.CRITICAL)

    if CFG['general']['log_level'] not in ['INFO', 'WARN', 'ERROR', 'DEBUG', 'CRITICAL' ]:
        print >>sys.stderr, 'Log level: ' + CFG['general']['log_level'] + ' is invalid'

    if CFG['general']['log_level'] == 'INFO':
        logging.getLogger().setLevel(logging.INFO)
    elif CFG['general']['log_level'] == 'WARN':
        logging.getLogger().setLevel(logging.WARNING)
    elif CFG['general']['log_level'] == 'ERROR':
        logging.getLogger().setLevel(logging.ERROR)
    elif CFG['general']['log_level'] == 'DEBUG':
        logging.getLogger().setLevel(logging.DEBUG)
    elif CFG['general']['log_level'] == 'CRITICAL':
        logging.getLogger().setLevel(logging.CRITICAL)

def initialize():

    get_config()
    set_logging()

    foreground=False
    logfile='/var/log/ec2_collective.log'
    pidfile='/var/run/ec2_collectived.pid'

    try:
        opts, args = getopt.getopt(sys.argv[1:], 'hfl:p:', ['foreground', 'help', 'logfile', 'pidfile'])

    except getopt.GetoptError, err:
        print >>sys.stderr, str(err) 
        return 1

    for o, a in opts:
        if o in ('-h', '--help'):
            usage()
        elif o in ('-f', '--foreground'):
            foreground=True
        elif o in ('-l', '--logfile'):
            logfile=a
        elif o in ('-p', '--pidfile'):
            pidfile=a

    return (foreground, logfile, pidfile)

def get_yaml_facts (yaml_file):
    dataMap = {}

    if not os.path.exists(yaml_file):
        logging.error( yaml_file + ' file does not exist')
        sys.exit(1)

    stat = os.stat(yaml_file)
    fileage = int(stat.st_mtime)

    f = open(yaml_file, 'r')
    dataMap = yaml.safe_load(f)
    f.close()

    if len(dataMap) > 0:
        return (fileage, dataMap)
    else:
	return (fileage, None)

def update_yaml_facts (yf_last_update, yaml_file, yaml_facts):

    # Get file info
    stat = os.stat(yaml_file)
    fileage = int(stat.st_mtime)

    if fileage != yf_last_update:
	yf_last_update, dataMap = get_yaml_facts(yaml_file)
	return (fileage, dataMap)
    else:
	return (fileage, yaml_facts)

def cli_func (message):

	try:
           logging.debug ('Performing: ' + str(message['cmd']))
           o = subprocess.Popen(message['cmd'], shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
           output = o.communicate()[0]
           rc = o.poll()

        except OSError, e:
            output = ('Failed to execute ' + message['cmd'] + ' (%d) %s \n' % (e.errno, e.strerror))
            rc = e.errno 

        response={'func': message['func'], 'output': output, 'rc': rc, 'ts':time.time(), 'msg_id':message['orgid'], 'hostname':gethostname()};
        return response

def write_to_file (payload, identifier):
    script_file = '/tmp/' + identifier

    try:
        f = open (script_file, 'w')
        f.write (payload)
    except IOError, e:
        logging.error ('Failed to write paytload to ' + script_file + ' (%d) %s \n' % (e.errno, e.strerror))
        return False

    os.chmod(script_file, stat.S_IRWXU)

    return True
    

def script_func (message):

    if not write_to_file(message['payload'], message['batch_msg_id']) :
        response={'func': message['func'], 'output': 'Failed to write script file', 'rc': '255', 'ts':time.time(), 'msg_id':message['orgid'], 'hostname':gethostname()};
        return response

    try:
        logging.debug ('Performing: sript execution')
        o = subprocess.Popen('/tmp/' + message['batch_msg_id'], shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        output = o.communicate()[0]
        rc = o.poll()

    except OSError, e:
        output = ('Failed to execute ' + message['cmd'] + ' (%d) %s \n' % (e.errno, e.strerror))
        rc = e.errno 

    response={'func': message['func'], 'output': output, 'rc': rc, 'ts':time.time(), 'msg_id':message['orgid'], 'hostname':gethostname()};
    return response

def receive_sql_msg ( read_msgs, read_queue, yaml_facts ):

    new_msgs = []

    msgs = read_queue.get_messages(num_messages=10, visibility_timeout=0)

    for msg in msgs:

	if msg.id in read_msgs:
            continue
        else:
            read_msgs[msg.id] = msg.id
   
        message=json.loads(msg.get_body())
        batch_msg_id = message['batch_msg_id']
        wf = message['wf']
        wof = message['wof']
        message['orgid'] = msg.id

        # We send multiple duplicate messages - lets avoid handling all of them
	if batch_msg_id in read_msgs:
            continue
        else:
            read_msgs[batch_msg_id] = batch_msg_id
  
	if fact_lookup(wf, wof, yaml_facts):
            read_msgs[msg.id] = msg.id
            continue

        logging.debug('New valid SQS message received')
	new_msgs.append(message)

    return read_msgs, new_msgs

def process_msg ( message ):

    cmd_str = str(message['cmd'])
    func = str(message['func'])
    ts = str(message['ts'])
  
    if func in [ 'discovery', 'ping', 'count' ]:
        logging.debug("Performing ping reply")
        response={'func': func, 'output': ts, 'rc': '0', 'ts':time.time(), 'msg_id':message['orgid'], 'hostname':gethostname()};
    elif func == 'cli':
        logging.debug("Performing command execution")
        response = cli_func(message)
    elif func == 'script':
        logging.debug("Performing script execution")
        response = script_func (message) 
    else:
        response =  'Unknown command ' + cmd_str
        response={'func': func, 'output': response, 'rc': '0', 'ts':time.time(), 'msg_id':message['orgid'], 'hostname':gethostname()};

    return response

def receive_queue_msg ( task_queue, done_queue ):

    for message in iter(task_queue.get, 'STOP'):
        logging.debug('Task read from task queue')
        response = process_msg(message)
        done_queue.put(response)

    logging.debug('Worker received STOP signal - terminating')

def write_sqs_msg (response, write_queue):
 
    response=json.dumps(response)
    message = write_queue.new_message(response)
    
    # Write message 5 times to make sure receiver gets it
    written=False
    for i in range(0, 3):
        org = write_queue.write(message)
        if org.id is None and written is False:
            logging.error ('Failed to write response message to SQS')
            del read_msgs[msg.id]
        else:
            logging.debug('Wrote response to SQS')
            written=True

    return written

def get_config():
    # CFILE 
    if not os.path.exists(CFILE):
        logging.error ( CFILE + ' file does not exist')
        sys.exit(1)
    
    try:
        f = open(CFILE, 'r')
    except IOError, e:
        logging.error ('Failed to execute ' + message['cmd'] + ' (%d) %s \n' % (e.errno, e.strerror))
    
    try:
        global CFG
        CFG=json.load(f)
    except (TypeError, ValueError), e:
        logging.error ('Error in configuration file')
        sys.exit(1)

def daemonize (foreground, pidfile, stdin='/dev/null', stdout='/dev/null', stderr='/dev/null' ):

    if foreground is True:
        logging.info ('Running in the foreground')
        return

    try:
        pid = os.fork( )
        if pid > 0:
            sys.exit(0) # Exit first parent.
    except OSError, e:
        sys.stderr.write("fork #1 failed: (%d) %s\n" % (e.errno, e.strerror))
        sys.exit(1)
    # Decouple from parent environment.
    os.chdir("/")
    os.umask(0)
    os.setsid( )
    # Perform second fork.
    try:
        pid = os.fork( )
        if pid > 0:
            sys.exit(0) # Exit second parent.
    except OSError, e:
        sys.stderr.write("fork #2 failed: (%d) %s\n" % (e.errno, e.strerror))
        sys.exit(1)
    # The process is now daemonized, redirect standard file descriptors.
    for f in sys.stdout, sys.stderr: f.flush( )
    si = file(stdin, 'r')
    so = file(stdout, 'a+')
    se = file(stderr, 'a+', 0)
    os.dup2(si.fileno( ), sys.stdin.fileno( ))
    os.dup2(so.fileno( ), sys.stdout.fileno( ))
    os.dup2(se.fileno( ), sys.stderr.fileno( ))

    try:
        f = open (pidfile, 'w')
        f.write (str(os.getpid()))
    except IOError, e:
        sys.stderr.write("Failed to write pid to pidfile (%s): (%d) %s\n" % (pidfile, e.errno, e.strerror))
        sys.exit(1)

    sys.stdout.write('Daemon started with pid %d\n' % os.getpid( ) )

    sys.stderr.flush()
    sys.stdout.flush()

def fact_lookup (wf, wof, yaml_facts):
    # WOF
    # Return True if we have the fact ( just on should skip message )
    # Return False if we don't have the fact

    # WF
    # Return False if we have all the fact ( all facts must match )
    # Return True if we don't have the fact

    # If nothing is set we process message
    if wf is None and wof is None:
        return False

    # If wof is in facts we return True ( skip message )
    if wof is not None:
        wof = wof.split(',')
        for f in wof:
            if '=' in f:
                f = f.split('=')
    
                if (f[0] in yaml_facts) and (yaml_facts[f[0]] == f[1]):
                    return True
            else:
                if f in yaml_facts:
    		    return True

    # Without is set but we did not find it, if wf is not set we return False ( process message )
    if wf is None:
        return False

    # If all wf is in facts we return False ( process message )
    no_match=0
    wf = wf.split(',')
    for f in wf:
        if '=' in f:
            f = f.split('=')
   
            if (f[0] not in yaml_facts) or (yaml_facts[f[0]] != f[1]):
                no_match += 1
        else:
            if f not in yaml_facts:
                no_match += 1

    if no_match == 0 :
        # All facts was found ( process message )
        return False
    else:
        # Facts set was not found - return True ( skip message )
        return True

def main ():

    # Get facts
    if CFG['general']['yaml_facts'] == 'True':
        yf_last_update, yaml_facts = get_yaml_facts(CFG['general']['yaml_facts_path'])
    else:
        yaml_facts = None

    # Connect with key, secret and region
    conn = connect_to_region(CFG['aws']['region'])
    read_queue = conn.get_queue(CFG['aws']['read_queue'])
    write_queue = conn.get_queue(CFG['aws']['write_queue'])

    # Create queues
    task_queue = Queue()
    done_queue = Queue()
    
    # Read from master
    read_msgs={}
   
    start_time=int(time.time()) 
    last_read=time.time()
    new_msgs = False
    while ( True ):

        # See if we need to update facts file
        if (int(time.time()) - start_time ) > CFG['general']['yaml_facts_refresh']:
            logging.debug('Reloading yaml facts')
	    start_time=time.time()
	    yf_last_update, yaml_facts = update_yaml_facts(yf_last_update, CFG['general']['yaml_facts_path'], yaml_facts )

        # Do not poll SQS too often, that would be too expensive
        if (time.time() - last_read) > CFG['general']['sqs_poll_interval']:
            logging.debug('Looking for new messages on SQS')
            read_msgs, new_msgs = receive_sql_msg ( read_msgs, read_queue, yaml_facts )

            # If any messages are received we put the data on the task queue
            if new_msgs:
                for new_msg in new_msgs:
                    logging.debug('Putting SQS message on task queue')
                    task_queue.put(new_msg)
                    logging.debug('Forking worker')
                    Process(target=receive_queue_msg, args=(task_queue, done_queue)).start()

            last_read=time.time()
        else:
           time.sleep(0.5) 

        #if new_msgs:
        try:
            response = done_queue.get(False)
            task_queue.put('STOP')
            logging.debug('Response read from done queue')
            write_sqs_msg (response, write_queue)
        except Empty:
            logging.debug('Done queue is empty')

        # Join finished children
        running_children = len(active_children())
        logging.debug(str(running_children) + ' active children')

if __name__ == "__main__":

    (foreground, logfile, pidfile)=initialize()
    daemonize(foreground, pidfile,'/dev/null', logfile, logfile)
    try:
        sys.exit(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info('Dutifully exiting...')
        sys.exit(0)
