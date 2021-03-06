class ec2_collective::params {
    $run_as                     = 'root'
    $release                    = '2014070100'
    $install_deps               = true
    $boto_version               = '2.27.0'
    $deps_from_pip              = false
    $install_agent              = true
    $install_master             = false
    $log_level                   = 'INFO'
    $fact_files                 = ['/etc/facts/facts.yaml', '/var/lib/puppet/state/classes.txt']
    $agent_read_queue           = 'testing_master'
    $agent_write_queue          = 'testing_agent'
    $agent_facts_queue          = 'testing_facts'
    $default_queue_name         = 'testing'
    $master_write_suffix        = '_master'
    $maste_read_suffix          = '_agent'
    $master_facts_suffix        = '_facts'
}
