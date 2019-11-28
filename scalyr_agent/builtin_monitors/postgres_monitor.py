# Copyright 2015 Scalyr Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License")
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ------------------------------------------------------------------------
#
# A ScalyrMonitor which monitors the status of PostgreSQL databases.
#
# Note, this can be run in standalone mode by:
#     python -m scalyr_agent.run_monitor scalyr_agent.builtin_monitors.mysql_monitor
import sys
import re
import os
import stat
import errno
import string
from datetime import datetime

from scalyr_agent import ScalyrMonitor, UnsupportedSystem, define_config_option, define_metric, define_log_field

# We must require 2.5 or greater right now because pg8000 requires it.
if sys.version_info[0] < 2 or (sys.version_info[0] == 2 and sys.version_info[1] < 5):
    raise UnsupportedSystem('postgresql_monitor', 'Requires Python 2.5 or greater.')

import pg8000

__monitor__ = __name__


define_config_option(__monitor__, 'module',
                     'Always ``scalyr_agent.builtin_monitors.postgres_monitor ``', required_option=True)
define_config_option(__monitor__, 'id',
                     'Optional. Included in each log message generated by this monitor, as a field named ``instance``. '
                     'Allows you to distinguish between values recorded by different monitors. This is especially '
                     'useful if you are running multiple PostgreSQL instances on a single server; you can monitor each '
                     'instance with a separate postgresql_monitor record in the Scalyr Agent configuration.',
                     convert_to=str)
define_config_option(__monitor__, 'database_host',
                     'Name of host machine the agent will connect to PostgreSQL to retrieve monitoring data.',
                     convert_to=str)
define_config_option(__monitor__, 'database_port',
                     'Name of port on the host machine the agent will connect to PostgreSQL to retrieve monitoring data.',
                     convert_to=str)
define_config_option(__monitor__, 'database_name',
                     'Name of database the agent will connect to PostgreSQL to retrieve monitoring data.',
                     convert_to=str)
define_config_option(__monitor__, 'database_username',
                     'Username which the agent uses to connect to PostgreSQL to retrieve monitoring data.',
                     convert_to=str)
define_config_option(__monitor__, 'database_password',
                     'Password for connecting to PostgreSQL.', 
                     convert_to=str)

# Metric definitions.
define_metric(__monitor__, 'postgres.database.connections',
              'The number of current active connections.  The value is accurate to when the check was made.'
              , cumulative=False, category='connections')
define_metric(__monitor__, 'postgres.database.transactions',
              'The number of database transactions that have been committed.  '
              'The value is relative to postgres.database.stats_reset.'
              , extra_fields={'result': 'committed'}, cumulative=True, category='general')
define_metric(__monitor__, 'postgres.database.transactions',
              'The number of database transactions that have been rolled back.  '
              'The value is relative to postgres.database.stats_reset.'
              , extra_fields={'result': 'rolledback'}, cumulative=True, category='general')
define_metric(__monitor__, 'postgres.database.disk_blocks',
              'The number of disk blocks read into the database.  '
              'The value is relative to postgres.database.stats_reset.'
              , extra_fields={'type': 'read'}, cumulative=True, category='general')
define_metric(__monitor__, 'postgres.database.disk_blocks',
              'The number of disk blocks read that were found in the buffer cache.  '
              'The value is relative to postgres.database.stats_reset.'
              , extra_fields={'type': 'hit'}, cumulative=True, category='general')
define_metric(__monitor__, 'postgres.database.query_rows',
              'The number of rows returned by all queries in the database.  '
              'The value is relative to postgres.database.stats_reset.'
              , extra_fields={'op': 'returned'}, cumulative=True, category='general')
define_metric(__monitor__, 'postgres.database.query_rows',
              'The number of rows fetched by all queries in the database.  '
              'The value is relative to postgres.database.stats_reset.'
              , extra_fields={'op': 'fetched'}, cumulative=True, category='general')
define_metric(__monitor__, 'postgres.database.query_rows',
              'The number of rows inserted by all queries in the database.  '
              'The value is relative to postgres.database.stats_reset.'
              , extra_fields={'op': 'inserted'}, cumulative=True, category='general')
define_metric(__monitor__, 'postgres.database.query_rows',
              'The number of rows updated by all queries in the database.  '
              'The value is relative to postgres.database.stats_reset.'
              , extra_fields={'op': 'updated'}, cumulative=True, category='general')
define_metric(__monitor__, 'postgres.database.query_rows',
              'The number of rows deleted by all queries in the database.  '
              'The value is relative to postgres.database.stats_reset.'
              , extra_fields={'op': 'deleted'}, cumulative=True, category='general')
define_metric(__monitor__, 'postgres.database.temp_files',
              'The number of temporary files created by queries to the database.  '
              'The value is relative to postgres.database.stats_reset.'
              , cumulative=True, category='general')
define_metric(__monitor__, 'postgres.database.temp_bytes',
              'The total amount of data written to temporary files by queries to the database.  '
              'The value is relative to postgres.database.stats_reset.'
              , cumulative=True, category='general')
define_metric(__monitor__, 'postgres.database.deadlocks',
              'The number of deadlocks detected in the database.  '
              'The value is relative to postgres.database.stats_reset.'
              , cumulative=True, category='general')
define_metric(__monitor__, 'postgres.database.blocks_op_time',
              'The amount of time data file blocks are read by clients in the database (in milliseconds).  '
              'The value is relative to postgres.database.stats_reset.'
              , extra_fields={'op': 'read'}, cumulative=True, category='general')
define_metric(__monitor__, 'postgres.database.blocks_op_time',
              'The amount of time data file blocks are written by clients in the database (in milliseconds).  '
              'The value is relative to postgres.database.stats_reset.'
              , extra_fields={'op': 'write'}, cumulative=True, category='general')
define_metric(__monitor__, 'postgres.database.stats_reset',
              'The time at which database statistics were last reset.'
              , cumulative=False, category='general')
define_metric(__monitor__, 'postgres.database.size', 'The number of bytes the database is taking up on disk.' , cumulative=False, category='general')

define_log_field(__monitor__, 'monitor', 'Always ``postgres_monitor``.')
define_log_field(__monitor__, 'instance', 'The ``id`` value from the monitor configuration.')
define_log_field(__monitor__, 'metric', 'The name of a metric being measured, e.g. "postgres.vars".')
define_log_field(__monitor__, 'value', 'The metric value.')


class PostgreSQLDb(object):
    """ Represents a PopstgreSQL database
    """
    
    _database_stats =  {
        'pg_stat_database': {
          'numbackends' : ['postgres.database.connections'],
          'xact_commit' : ['postgres.database.transactions', 'result', 'committed'],
          'xact_rollback' : ['postgres.database.transactions', 'result', 'rolledback'],
          'blks_read' : ['postgres.database.disk_blocks', 'type', 'read'],
          'blks_hit' : ['postgres.database.disk_blocks', 'type', 'hit'],
          'tup_returned' : ['postgres.database.query_rows', 'op', 'returned'],
          'tup_fetched' : ['postgres.database.query_rows', 'op', 'fetched'],
          'tup_inserted' : ['postgres.database.query_rows', 'op', 'inserted'],
          'tup_updated' : ['postgres.database.query_rows', 'op', 'updated'],
          'tup_deleted' : ['postgres.database.query_rows', 'op', 'deleted'],
          'temp_files' : ['postgres.database.temp_files'],
          'temp_bytes' : ['postgres.database.temp_bytes'],
          'deadlocks' : ['postgres.database.deadlocks'],
          'blk_read_time' : ['postgres.database.blocks_op_time', 'op', 'read'],
          'blk_write_time' : ['postgres.database.blocks_op_time', 'op', 'write'],
          'stats_reset' : ['postgres.database.stats_reset']
        }
    }
    
    def connect(self):
        try:
            conn = pg8000.connect(user = self._user, host = self._host, port = self._port,
                                  database = self._database, password = self._password)
            self._db = conn
            self._cursor = self._db.cursor()
            self._gather_db_information()                                                   
        except pg8000.Error, me:
            self._db = None
            self._cursor = None
            self._logger.error("Database connect failed: %s" % me)
        except Exception, ex:
            self._logger.error("Exception trying to connect occured:  %s" % ex)
            raise Exception("Exception trying to connect:  %s" % ex)
        
    def is_connected(self):
        """returns True if the database is connected"""
        return self._db is not None

    def close(self):
        """Closes the cursor and connection to this PostgreSQL server."""
        if self._cursor:
            self._cursor.close()
        if self._db:
            self._db.close()
        self._cursor = None
        self._db = None
            
    def reconnect(self):
        """Reconnects to this PostgreSQL server."""
        self.close()
        self.connect()
        
    def _get_version(self):
        version = "unknown"
        try:
            self._cursor.execute("select version();")
            r = self._cursor.fetchone()
            # assumes version is in the form of 'PostgreSQL x.y.z on ...'
            s = string.split(r[0])
            version = s[1]
        except:
            ex = sys.exc_info()[0]
            self._logger.error("Exception getting database version: %s" % ex)
        return version
        
    def _gather_db_information(self):
        self._version = self._get_version()
        try:
            if self._version == "unknown":
              self._major = self._medium = self._minor = 0
            else:
                version = self._version.split(".")
                self._major = int(version[0])
                self._medium = int(version[1])
        except (ValueError, IndexError), e:
            self._major = self._medium = 0
            self._version = "unknown"
        except:
            ex = sys.exc_info()[0]
            self._logger.error("Exception getting database version: %s" % ex)
            self._version = "unknown"
            self._major = self._medium = 0

    def _fields_and_data_to_dict(self, fields, data):
        result = {}
        for f, d in zip(fields, data):
            result[f] = d
        return result

    def _retrieve_database_table_stats(self, table):
        try:
            # get the fields and values
            self._cursor.execute("select * from %s where datname = '%s' limit 0;" % (table, self._database))
            fields = [desc[0] for desc in self._cursor.description];
            self._cursor.execute("select * from %s where datname = '%s';" % (table, self._database))
            data = self._cursor.fetchone()
        except pg8000.OperationalError, (errcode, msg):
            if errcode != 2006:  # "PostgreSQL server has gone away"
                raise Exception("Database error -- " + errcode)
            self.reconnect()
            return None
  
        # combine the fields and data
        data = self._fields_and_data_to_dict(fields, data)
  
        # extract the ones we want
        dict = {}
        for i in self._database_stats[table].keys():
            if i in data:
                dict[i] = data[i];
        return dict
        
    def retrieve_database_stats(self):
        result = {}
        for i in self._database_stats.keys():
            tmp = self._retrieve_database_table_stats(i);
            if tmp != None:
                result.update(tmp)
        return result
        
    def retrieve_database_size(self):
        try:
            self._cursor.execute("select pg_database_size('%s');" % self._database)
            size = self._cursor.fetchone()[0]
        except pg8000.OperationalError, (errcode, msg):
            if errcode != 2006:  # "PostgreSQL server has gone away"
                raise Exception("Database error -- " + errcode)
            self.reconnect()
            return None
        return size          
          
    def __str__(self):
        return "DB(%r:%r, %r)" % (self._host, self._port, self._version)
            
    def __repr__(self):
        return self.__str__()
   
    def __init__(self, host, port, database, username, password, logger = None):
        """Constructor: 
    
        @param database: database we are connecting to
        @param host: database host being connected to
        @param port: database port being connected to
        @param username: username to connect with
        @param password: password to establish connection
        """

        self._host = host
        self._port = port
        self._database = database
        self._user = username
        self._password = password
        self._logger = logger

        self._db = None
        self._cursor = None

class PostgresMonitor(ScalyrMonitor):
    """
# PostgreSQL Monitor

The PostgreSQL monitor allows you to collect data about the usage and performance of your PostgreSQL server.

Each monitor can be configured to monitor a specific PostgreSQL database, thus allowing you to configure alerts and the
dashboard entries independently (if desired) for each instance.

## Configuring PostgreSQL

To use Scalyr's PostgreSQL monitor, you will first need to configure your PostgreSQL server to enable password
authentication for a user as well as configure a user to connect to the database and gather the necessary data.

To configure PostgreSQL for password authentication, you will need a line like the following in your pg_hba.conf
file:

    host    all             all             127.0.0.1/32            md5
    
To create a user and enable password authentication, please consult the PostgreSQL documentation.  For version
9.3, that documentation can be found here:  http://www.postgresql.org/docs/9.3/static/sql-createrole.html

### Configuring the PostgreSQL monitor

The PostgreSQL monitor is included with the Scalyr agent.  In order to configure it, you will need to add its monitor
configuration to the Scalyr agent config file.

A basic PostgreSQL monitor configuration entry might resemble:

  monitors: [
    {
      module:              "scalyr_agent.builtin_monitors.postgres_monitor",
      id:                  "mydb",
      database_host:       "localhost",
      database_name:       "<database>",
      database_username:   "<username>",
      database_password:   "<password>"
    }
  ]
  
Note the ``id`` field in the configurations.  This is an optional field that allows you to specify an identifier
specific to a particular instance of PostgreSQL and will make it easier to filter on metrics specific to that
instance."""
    
    
    def _initialize(self):
        """Performs monitor-specific initialization.
        """

        # Useful instance variables:
        #   _sample_interval_secs:  The number of seconds between calls to gather_sample.
        #   _config:  The dict containing the configuration for this monitor instance as retrieved from configuration
        #             file.
        #   _logger:  The logger instance to report errors/warnings/etc.
        
        # determine how we are going to connect
        database = None
        host = "localhost"
        port = 5432
        username = None
        password = None
        if "database_host" in self._config:
            host = self._config["database_host"]
        if "database_port" in self._config:
            port = self._config["database_port"]
        if "database_name" in self._config:
            database = self._config["database_name"]
        if "database_username" in self._config:
            username = self._config["database_username"]
        if "database_password" in self._config:
            password = self._config["database_password"]
        
        if "database_name" not in self._config or "database_username" not in self._config or "database_password" not in self._config:
            raise Exception("database_name, database_username and database_password must be specified in the configuration.")

        self._db = PostgreSQLDb ( database = database,
                                  host = host,
                                  port = port,
                                  username = username,
                                  password = password,
                                  logger = self._logger )


    def gather_sample(self):
        """Invoked once per sample interval to gather a statistic.
        """
        
        def timestamp_ms(dt):
            epoch = datetime(1970, 1, 1, 0, 0, 0, 0)
            dt = dt.replace(tzinfo=None)
            td = dt - epoch
            return (td.microseconds + (td.seconds + td.days * 24 * 3600) * 1000000) / 1000

        try:
            self._db.reconnect()
        except Exception, e:
            self._logger.warning( "Unable to gather stats for postgres database - %s" % str(e) )
            return

        if not self._db.is_connected():
            self._logger.warning( "Unable to gather stats for postgres database - unable to connect to database" )
            return
        
        dbsize = self._db.retrieve_database_size()
        if dbsize != None:
            self._logger.emit_value('postgres.database.size', dbsize)
        dbstats = self._db.retrieve_database_stats()
        if dbstats != None:
            for table in self._db._database_stats.keys():
                for key in self._db._database_stats[table].keys():
                    if key in dbstats.keys():
                        if key != "stats_reset":
                            extra = None
                            if len(self._db._database_stats[table][key]) == 3:
                                extra = { }
                                extra[self._db._database_stats[table][key][1]] = self._db._database_stats[table][key][2]
                            self._logger.emit_value(self._db._database_stats[table][key][0], dbstats[key], extra)
                        else:
                            self._logger.emit_value(self._db._database_stats[table][key][0], timestamp_ms(dbstats[key]))
        # Database statistics are constant for the duration of a transaction, and by default, the
        # database runs all queries for a connection under a single transaction.  If we don't close
        # the connection then next gather sample we will still hold the same connection, which is
        # still the same transaction, and no statistics will have been updated.
        # Closing the connection also means that we are not needlessly holding an idle connection
        # for the duration of the gather sample interval.
        self._db.close()
