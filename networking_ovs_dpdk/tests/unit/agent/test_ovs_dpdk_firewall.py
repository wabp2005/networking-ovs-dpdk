# Copyright 2012, Nachi Ueno, NTT MCL, Inc.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import copy
import mock
import six

from networking_ovs_dpdk.agent import ovs_dpdk_firewall
from neutron.agent.common import ovs_lib
from neutron.conf.agent import securitygroups_rpc as sg_cfg
from neutron.plugins.ml2.drivers.openvswitch.agent.openflow.ovs_ofctl \
    import br_int
from neutron.tests import base
from oslo_config import cfg

IPv4 = "IPv4"
IPv6 = "IPv6"

FAKE_PREFIX = {IPv4: '10.0.0.0/24',
               IPv6: 'fe80::/48'}
FAKE_IP = {IPv4: '10.0.0.1',
           IPv6: 'fe80::1'}

FAKE_SGID = 'fake_sgid'
OTHER_SGID = 'other_sgid'
SEGMENTATION_ID = "1402"
TAG_ID = '1'

# List of protocols.
PROTOCOLS = {IPv4: {'tcp': 'eth_type=0x0800,ip_proto=6',
                'udp': 'eth_type=0x0800,ip_proto=17',
                'ip': 'eth_type=0x0800',
                'icmp': 'eth_type=0x0800,ip_proto=1',
                'igmp': 'eth_type=0x0800,ip_proto=2',
                'arp': 'arp'},
             IPv6: {'tcp': 'eth_type=0x86dd,ip_proto=6',
                'udp': 'eth_type=0x86dd,ip_proto=17',
                'ip': 'eth_type=0x86dd',
                'ipv6': 'eth_type=0x86dd',
                'icmp': 'eth_type=0x86dd,ip_proto=58',
                'icmpv6': 'eth_type=0x86dd,ip_proto=58',
                'arp': 'arp'}}
PROTOCOLS_DEFAULT_PRIO = {'tcp': 100,
                          'udp': 100,
                          'ip': 90,
                          'ipv6': 90,
                          'icmp': 90}
PROTOCOLS_LEARN_ACTION_PRIO = {'tcp': 90,
                               'udp': 90,
                               'ip': 90,
                               'icmp': 90}
PROTOCOLS_DEST = {'tcp': 'NXM_OF_TCP_DST[]=NXM_OF_TCP_SRC[],',
                  'udp': 'NXM_OF_UDP_DST[]=NXM_OF_UDP_SRC[],',
                  'ip': '',
                  'ipv6': '',
                  'icmp': ''}

PROTOCOLS_SRC = {'tcp': 'NXM_OF_TCP_SRC[]=NXM_OF_TCP_DST[],',
                 'udp': 'NXM_OF_UDP_SRC[]=NXM_OF_UDP_DST[],',
                 'ip': '',
                 'ipv6': '',
                 'icmp': ''}

IDLE_TIMEOUT = 30
HARD_TIMEOUT = 1800

# OpenFlow Table IDs
OF_ZERO_TABLE = 0
OF_SELECT_TABLE = 71
OF_EGRESS_TABLE = 72
OF_INGRESS_TABLE = 73
OF_INGRESS_EXT_TABLE = 81

# From networking_ovs_dpdk.common.config
DEFAULT_BRIDGE_MAPPINGS = []
ovs_opts = [
    cfg.StrOpt('integration_bridge', default='br-int',
               help="Integration bridge to use."),
    cfg.StrOpt('tunnel_bridge', default='br-tun',
               help="Tunnel bridge to use."),
    cfg.StrOpt('int_peer_patch_port', default='patch-tun',
               help="Peer patch port in integration bridge for tunnel "
                    "bridge."),
    cfg.StrOpt('tun_peer_patch_port', default='patch-int',
               help="Peer patch port in tunnel bridge for integration "
                    "bridge."),
    cfg.IPOpt('local_ip', version=4,
              help="Local IP address of tunnel endpoint."),
    cfg.ListOpt('bridge_mappings',
                default=DEFAULT_BRIDGE_MAPPINGS,
                help="List of <physical_network>:<bridge>. "
                     "Deprecated for ofagent."),
    cfg.BoolOpt('use_veth_interconnection', default=False,
                help="Use veths instead of patch ports to interconnect the "
                     "integration bridge to physical bridges.")
]

COOKIE = 1


class BaseOVSDPDKFirewallTestCase(base.BaseTestCase):
    def setUp(self):
        super(BaseOVSDPDKFirewallTestCase, self).setUp()
        cfg.CONF.register_opts(sg_cfg.security_group_opts, 'SECURITYGROUP')
        cfg.CONF.register_opts(ovs_opts, "OVS")
        conn_patcher = mock.patch('neutron.agent.ovsdb.impl_idl._connection')
        conn_patcher.start()
        self.addCleanup(conn_patcher.stop)
        int_br = br_int.OVSIntegrationBridge(cfg.CONF.OVS.integration_bridge)
        self.firewall = ovs_dpdk_firewall.OVSFirewallDriver(int_br)


class OVSDPDKFirewallTestCase(BaseOVSDPDKFirewallTestCase):
    def setUp(self):
        super(OVSDPDKFirewallTestCase, self).setUp()
        # NOTE(ralonsoh): by default, OVSFirewallDriver._deferred = False,
        #                 therefore neutron.agent.common.ovs_lib.OVSBridge is
        #                 used.
        self._mock_add_flow = \
            mock.patch.object(ovs_lib.OVSBridge, "add_flow")
        self.mock_add_flow = self._mock_add_flow.start()
        self._mock_delete_flows = \
            mock.patch.object(ovs_lib.OVSBridge, "delete_flows")
        self.mock_delete_flows = self._mock_delete_flows.start()
        self._mock_get_vif_port_by_id = \
            mock.patch.object(ovs_lib.OVSBridge, "get_vif_port_by_id")
        self.mock_get_vif_port_by_id = self._mock_get_vif_port_by_id.start()
        self._mock_db_get_val =\
            mock.patch.object(ovs_lib.OVSBridge, "db_get_val")
        self.mock_db_get_val = self._mock_db_get_val.start()

        # Create a fake port.
        self.fake_port_1 = self._fake_port(name='tapfake_dev_1')
        # Mock the VifPort.
        self.mock_get_vif_port_by_id.return_value = \
            self._fake_vifport(self.fake_port_1)
        self.mport = ['0x0020/0xffe0', '0x0010/0xfff0', '0x000c/0xfffc',
                      '0x000a/0xfffe', '0x0040/0xffe0', '0x0060/0xfffc',
                      '0x0064']

    def tearDown(self):
        super(OVSDPDKFirewallTestCase, self).tearDown()
        self._mock_add_flow.stop()
        self._mock_delete_flows.stop()
        self._mock_get_vif_port_by_id.stop()
        self._mock_db_get_val.stop()

    def _fake_port(self, name,
                   ofport=1,
                   device='tapfake_dev_1',
                   mac='ff:ff:ff:ff:ff:ff',
                   sg_id=FAKE_SGID,
                   zone_id=1):
        return {'name': name,
                'ofport': ofport,
                'device': device,
                'mac_address': mac,
                'vinfo': {'tag': zone_id},
                'network_id': 'fake_net',
                'fixed_ips': [FAKE_IP[IPv4],
                              FAKE_IP[IPv6]],
                'security_groups': [sg_id],
                'security_group_source_groups': [sg_id]}

    def _fake_sg_rule_for_ethertype(self, ethertype, remote_group):
        return {'direction': 'ingress', 'remote_group_id': remote_group,
                'ethertype': ethertype}

    def _fake_sg_rules(self, sg_id=FAKE_SGID, remote_groups=None):
        remote_groups = remote_groups or {IPv4: [FAKE_SGID],
                                          IPv6: [FAKE_SGID]}
        rules = []
        for ip_version, remote_group_list in six.iteritems(remote_groups):
            for remote_group in remote_group_list:
                rules.append(self._fake_sg_rule_for_ethertype(ip_version,
                                                              remote_group))
        return {sg_id: rules}

    def _fake_sg_members(self, sg_ids=None):
        return {sg_id: copy.copy(FAKE_IP)
                for sg_id in (sg_ids or [FAKE_SGID])}

    def _fake_vifport(self, port):
        return ovs_lib.VifPort(port['name'],
                               port['ofport'],
                               port['device'],
                               port['mac_address'],
                               "br-%s" % port['device'])

    def _write_ip_src_dst(self, eth_type):
        # Source and destination IPs.
        if eth_type == IPv4:
            ip_dst = "NXM_OF_IP_DST[]=NXM_OF_IP_SRC[],"
            ip_src = "NXM_OF_IP_SRC[]=NXM_OF_IP_DST[],"
        else:
            ip_dst = "NXM_NX_IPV6_DST[]=NXM_NX_IPV6_SRC[],"
            ip_src = "NXM_NX_IPV6_SRC[]=NXM_NX_IPV6_DST[],"
        return ip_src, ip_dst

    def _write_proto(self, eth_type, protocol=None):
        return PROTOCOLS[eth_type][protocol]

    def _learn_egress_actions(self, protocol, ethertype, priority=None,
                       icmp_type=None, icmp_code=None):
        protocol_str = self._write_proto(ethertype, protocol)
        ip_src, ip_dst = self._write_ip_src_dst(ethertype)
        if not priority:
            priority = PROTOCOLS_DEFAULT_PRIO[protocol]
        port_destination = PROTOCOLS_DEST[protocol]
        port_source = PROTOCOLS_SRC[protocol]
        icmp_type_str = ""
        if icmp_type:
            icmp_type_str = 'icmp_type=%s,' % icmp_type
        icmp_code_str = ""
        if icmp_code:
            icmp_code_str = 'icmp_code=%s,' % icmp_code
        output_str = 'learn(table=%(table)s,' \
                     'priority=%(priority)s,' \
                     'idle_timeout=%(idle_timeout)s,' \
                     'hard_timeout=%(hard_timeout)s,' \
                     '%(protocol)s,' \
                     'NXM_OF_ETH_SRC[]=NXM_OF_ETH_DST[],' \
                     'NXM_OF_ETH_DST[]=NXM_OF_ETH_SRC[],' \
                     '%(ip_src)s' \
                     '%(ip_dst)s' \
                     '%(port_destination)s' \
                     '%(port_source)s' \
                     '%(icmp_type)s' \
                     '%(icmp_code)s' \
                     'NXM_OF_VLAN_TCI[0..11],' \
                     'load:NXM_NX_REG0[0..11]->NXM_OF_VLAN_TCI[0..11],' \
                     'output:NXM_OF_IN_PORT[]),' \
                     'resubmit(,%(r_table)s)' \
                     % {'table': OF_INGRESS_TABLE,
                        'priority': priority,
                        'idle_timeout': IDLE_TIMEOUT,
                        'hard_timeout': HARD_TIMEOUT,
                        'protocol': protocol_str,
                        'ip_src': ip_src,
                        'ip_dst': ip_dst,
                        'port_destination': port_destination,
                        'port_source': port_source,
                        'icmp_type': icmp_type_str,
                        'icmp_code': icmp_code_str,
                        'r_table': OF_INGRESS_TABLE}
        return output_str

    def _learn_ingress_actions(self, protocol, ethertype, priority=None,
                       icmp_type=None, icmp_code=None, ofport=1):
        protocol_str = PROTOCOLS[ethertype][protocol]
        ip_src, ip_dst = self._write_ip_src_dst(ethertype)
        if not priority:
            priority = PROTOCOLS_DEFAULT_PRIO[protocol]
        port_destination = PROTOCOLS_DEST[protocol]
        port_source = PROTOCOLS_SRC[protocol]
        icmp_type_str = ""
        if icmp_type:
            icmp_type_str = 'icmp_type=%s,' % icmp_type
        icmp_code_str = ""
        if icmp_code:
            icmp_code_str = 'icmp_code=%s,' % icmp_code
        output_str = 'learn(table=%(table)s,' \
                     'priority=%(priority)s,' \
                     'idle_timeout=%(idle_timeout)s,' \
                     'hard_timeout=%(hard_timeout)s,' \
                     '%(protocol)s,' \
                     'NXM_OF_ETH_SRC[]=NXM_OF_ETH_DST[],' \
                     'NXM_OF_ETH_DST[]=NXM_OF_ETH_SRC[],' \
                     '%(ip_src)s' \
                     '%(ip_dst)s' \
                     '%(port_destination)s' \
                     '%(port_source)s' \
                     '%(icmp_type)s' \
                     '%(icmp_code)s' \
                     'NXM_OF_VLAN_TCI[0..11],' \
                     'load:NXM_NX_REG0[0..11]->NXM_OF_VLAN_TCI[0..11],' \
                     'output:NXM_OF_IN_PORT[]),' \
                     'strip_vlan,output:%(ofport)s' \
                     % {'table': OF_EGRESS_TABLE,
                        'priority': priority,
                        'idle_timeout': IDLE_TIMEOUT,
                        'hard_timeout': HARD_TIMEOUT,
                        'protocol': protocol_str,
                        'ip_src': ip_src,
                        'ip_dst': ip_dst,
                        'port_destination': port_destination,
                        'port_source': port_source,
                        'icmp_type': icmp_type_str,
                        'icmp_code': icmp_code_str,
                        'ofport': ofport}
        return output_str

    def test_prepare_port_filter(self):
        # Setup rules and SG.
        self.firewall.sg_rules = self._fake_sg_rules()
        self.firewall.sg_members = {FAKE_SGID: {
            IPv4: ['10.0.0.1', '10.0.0.2'],
            IPv6: ['fe80::1']}}
        self.firewall.pre_sg_members = {}
        self.firewall._enable_multicast = True
        port = self.fake_port_1
        self.mock_db_get_val.side_effect = [
            {'net_uuid': "e00e6a6a-c88a-4724-80a7-6368a94241d9",
             'network_type': 'vlan',
             'physical_network': 'default',
             'segmentation_id': SEGMENTATION_ID,
             'tag': None},
            TAG_ID,
            'interface',
            {"segmentation_id": SEGMENTATION_ID}
            ]
        self.firewall.prepare_port_filter(port)

        calls_del_flows = [mock.call(dl_src=port['mac_address']),
                           mock.call(dl_dst=port['mac_address']),
                           mock.call(nw_dst=FAKE_IP[IPv4],
                                     proto='arp',
                                     table=OF_ZERO_TABLE),
                           mock.call(ipv6_dst=FAKE_IP[IPv6],
                                     proto=self._write_proto(IPv6, 'icmp'),
                                     table=OF_ZERO_TABLE)]
        self.mock_delete_flows.assert_has_calls(calls_del_flows,
                                                any_order=False)
        self.firewall._filtered_ports = port

        calls_add_flows = [
            mock.call(proto='arp',
                      actions='strip_vlan,output:%s' % port['ofport'],
                      dl_vlan=SEGMENTATION_ID,
                      nw_dst='%s' % FAKE_IP[IPv4], priority=100,
                      table=OF_ZERO_TABLE),
            mock.call(proto=self._write_proto(IPv6, 'icmp'),
                      actions='strip_vlan,output:%s' % port['ofport'],
                      icmpv6_type=133, priority=100, dl_vlan=SEGMENTATION_ID,
                      ipv6_dst=FAKE_IP[IPv6], table=OF_ZERO_TABLE),
            mock.call(proto=self._write_proto(IPv6, 'icmp'),
                      actions='strip_vlan,output:%s' % port['ofport'],
                      icmpv6_type=134, priority=100, dl_vlan=SEGMENTATION_ID,
                      ipv6_dst=FAKE_IP[IPv6], table=OF_ZERO_TABLE),
            mock.call(proto=self._write_proto(IPv6, 'icmp'),
                      actions='strip_vlan,output:%s' % port['ofport'],
                      icmpv6_type=135, priority=100, dl_vlan=SEGMENTATION_ID,
                      ipv6_dst=FAKE_IP[IPv6], table=OF_ZERO_TABLE),
            mock.call(proto=self._write_proto(IPv6, 'icmp'),
                      actions='strip_vlan,output:%s' % port['ofport'],
                      icmpv6_type=136, priority=100, dl_vlan=SEGMENTATION_ID,
                      ipv6_dst=FAKE_IP[IPv6], table=OF_ZERO_TABLE),
            mock.call(proto=self._write_proto(IPv6, 'icmp'),
                      actions='strip_vlan,output:%s' % port['ofport'],
                      icmpv6_type=137, priority=100, dl_vlan=SEGMENTATION_ID,
                      ipv6_dst=FAKE_IP[IPv6], table=OF_ZERO_TABLE),
            mock.call(proto='arp', actions='normal', priority=90,
                      table=OF_ZERO_TABLE),
            mock.call(proto=self._write_proto(IPv6, 'icmp'),
                      actions='normal', icmpv6_type=133, priority=90,
                      table=OF_ZERO_TABLE),
            mock.call(proto=self._write_proto(IPv6, 'icmp'),
                      actions='normal', icmpv6_type=134, priority=90,
                      table=OF_ZERO_TABLE),
            mock.call(proto=self._write_proto(IPv6, 'icmp'),
                      actions='normal', icmpv6_type=135, priority=90,
                      table=OF_ZERO_TABLE),
            mock.call(proto=self._write_proto(IPv6, 'icmp'),
                      actions='normal', icmpv6_type=136, priority=90,
                      table=OF_ZERO_TABLE),
            mock.call(proto=self._write_proto(IPv6, 'icmp'),
                      actions='normal', icmpv6_type=137, priority=90,
                      table=OF_ZERO_TABLE),
            mock.call(actions='mod_vlan_vid:%s,load:%s->NXM_NX_REG0[0..11],'
                              'load:0->NXM_NX_REG1[0..11],resubmit(,%s)' %
                              (TAG_ID, 0, OF_SELECT_TABLE),
                      priority=50, table=OF_ZERO_TABLE,
                      dl_src=port['mac_address']),
            mock.call(actions='mod_vlan_vid:%s,load:%s->NXM_NX_REG0[0..11],'
                              'load:0->NXM_NX_REG1[0..11],resubmit(,%s)' %
                              (TAG_ID, TAG_ID, OF_SELECT_TABLE),
                      priority=40, table=OF_ZERO_TABLE,
                      dl_vlan=SEGMENTATION_ID),
            mock.call(actions='drop', priority=35, table=OF_ZERO_TABLE),
            mock.call(proto=self._write_proto(IPv4, 'ip'),
                      dl_src=port['mac_address'],
                      actions='resubmit(,%s)' % OF_EGRESS_TABLE,
                      priority=100, table=OF_SELECT_TABLE, dl_vlan=TAG_ID,
                      nw_src='%s' % FAKE_IP[IPv4],
                      in_port=port['ofport']),
            mock.call(proto=self._write_proto(IPv6, 'ip'),
                      dl_src=port['mac_address'],
                      actions='resubmit(,%s)' % OF_EGRESS_TABLE,
                      priority=100, table=OF_SELECT_TABLE, dl_vlan=TAG_ID,
                      ipv6_src='%s' % FAKE_IP[IPv6],
                      in_port=port['ofport']),
            mock.call(priority=100, table=OF_SELECT_TABLE,
                      dl_dst=port['mac_address'], dl_vlan=TAG_ID,
                      actions='resubmit(,%s)' % OF_INGRESS_TABLE),
            mock.call(proto=self._write_proto(IPv4, 'ip'),
                      dl_src=port['mac_address'],
                      actions='resubmit(,%s)' % OF_EGRESS_TABLE,
                      priority=100, table=OF_SELECT_TABLE, dl_vlan=TAG_ID,
                      nw_src='0.0.0.0', in_port=port['ofport']),
            mock.call(priority=200, table=OF_SELECT_TABLE,
                      in_port=port['ofport'], dl_vlan=TAG_ID,
                      dl_dst='01:00:5e:00:00:00/01:00:5e:00:00:00',
                      dl_src=port['mac_address'],
                      nw_dst='224.0.0.0/4',
                      nw_src='%s' % FAKE_IP[IPv4],
                      proto=self._write_proto(IPv4, 'igmp'),
                      actions='strip_vlan,normal'),
            mock.call(priority=200, table=OF_SELECT_TABLE,
                      in_port=port['ofport'], dl_vlan=TAG_ID,
                      dl_dst='01:00:5e:00:00:00/01:00:5e:00:00:00',
                      dl_src=port['mac_address'],
                      ipv6_dst='ff00::/8',
                      ipv6_src='%s' % FAKE_IP[IPv6],
                      proto=self._write_proto(IPv6, 'icmp'),
                      actions='strip_vlan,normal'),
            mock.call(priority=190, table=OF_SELECT_TABLE,
                      dl_vlan=TAG_ID,
                      dl_dst='01:00:5e:00:00:00/01:00:5e:00:00:00',
                      nw_dst='224.0.0.0/4',
                      proto=self._write_proto(IPv4, 'igmp'),
                      actions='normal'),
            mock.call(priority=190, table=OF_SELECT_TABLE,
                      dl_vlan=TAG_ID,
                      dl_dst='01:00:5e:00:00:00/01:00:5e:00:00:00',
                      ipv6_dst='ff00::/8',
                      proto=self._write_proto(IPv6, 'icmp'),
                      actions='normal'),
            mock.call(priority=180, table=OF_SELECT_TABLE,
                      dl_vlan=TAG_ID, reg0=TAG_ID,
                      dl_dst='01:00:5e:00:00:00/01:00:5e:00:00:00',
                      nw_dst='224.0.0.0/4',
                      proto=self._write_proto(IPv4, 'tcp'),
                      actions='load:1->NXM_NX_REG1[0..11],resubmit(,%s)' %
                              OF_INGRESS_TABLE),
            mock.call(priority=180, table=OF_SELECT_TABLE,
                      dl_vlan=TAG_ID, reg0=TAG_ID,
                      dl_dst='01:00:5e:00:00:00/01:00:5e:00:00:00',
                      ipv6_dst='ff00::/8',
                      proto=self._write_proto(IPv6, 'tcp'),
                      actions='load:1->NXM_NX_REG1[0..11],resubmit(,%s)' %
                              OF_INGRESS_TABLE),
            mock.call(priority=180, table=OF_SELECT_TABLE,
                      dl_vlan=TAG_ID, reg0=TAG_ID,
                      dl_dst='01:00:5e:00:00:00/01:00:5e:00:00:00',
                      nw_dst='224.0.0.0/4',
                      proto=self._write_proto(IPv4, 'udp'),
                      actions='load:1->NXM_NX_REG1[0..11],resubmit(,%s)' %
                              OF_INGRESS_TABLE),
            mock.call(priority=180, table=OF_SELECT_TABLE,
                      dl_vlan=TAG_ID, reg0=TAG_ID,
                      dl_dst='01:00:5e:00:00:00/01:00:5e:00:00:00',
                      ipv6_dst='ff00::/8',
                      proto=self._write_proto(IPv6, 'udp'),
                      actions='load:1->NXM_NX_REG1[0..11],resubmit(,%s)' %
                              OF_INGRESS_TABLE),
            mock.call(priority=50, table=OF_SELECT_TABLE,
                      dl_vlan=TAG_ID, actions='drop',
                      proto=self._write_proto(IPv4, 'ip')),
            mock.call(priority=50, table=OF_SELECT_TABLE,
                      dl_vlan=TAG_ID, actions='drop',
                      proto=self._write_proto(IPv6, 'ip')),
            mock.call(actions='drop', in_port=port['ofport'], priority=40,
                      proto=self._write_proto(IPv4, 'udp'),
                      table=OF_EGRESS_TABLE, udp_dst=68,
                      udp_src=67, dl_vlan=TAG_ID),
            mock.call(actions='drop', in_port=port['ofport'], priority=40,
                      proto=self._write_proto(IPv6, 'udp'),
                      table=OF_EGRESS_TABLE, udp_dst=546,
                      udp_src=547, dl_vlan=TAG_ID),
            mock.call(actions='resubmit(,%s)' % OF_INGRESS_TABLE,
                      dl_src=port['mac_address'], in_port=port['ofport'],
                      priority=50,
                      proto=self._write_proto(IPv4, 'udp'),
                      table=OF_EGRESS_TABLE,
                      udp_dst=67, udp_src=68, dl_vlan=TAG_ID),
            mock.call(actions='resubmit(,%s)' % OF_INGRESS_TABLE,
                      dl_src=port['mac_address'], in_port=port['ofport'],
                      priority=50,
                      proto=self._write_proto(IPv6, 'udp'),
                      table=OF_EGRESS_TABLE,
                      udp_dst=547, udp_src=546, dl_vlan=TAG_ID),
            mock.call(icmp_type=9,
                      proto=self._write_proto(IPv4, 'icmp'),
                      dl_src=port['mac_address'],
                      actions='resubmit(,%s)' % OF_INGRESS_TABLE, priority=50,
                      table=OF_EGRESS_TABLE, dl_vlan=TAG_ID),
            mock.call(icmp_type=10,
                      proto=self._write_proto(IPv4, 'icmp'),
                      dl_src=port['mac_address'],
                      actions='resubmit(,%s)' % OF_INGRESS_TABLE, priority=50,
                      table=OF_EGRESS_TABLE, dl_vlan=TAG_ID),
            mock.call(icmpv6_type=130,
                      proto=self._write_proto(IPv6, 'icmp'),
                      dl_src=port['mac_address'],
                      actions='resubmit(,%s)' % OF_INGRESS_TABLE,
                      priority=50, table=OF_EGRESS_TABLE, dl_vlan=TAG_ID,
                      in_port=port['ofport']),
            mock.call(icmpv6_type=131,
                      proto=self._write_proto(IPv6, 'icmp'),
                      dl_src=port['mac_address'],
                      actions='resubmit(,%s)' % OF_INGRESS_TABLE,
                      priority=50, table=OF_EGRESS_TABLE, dl_vlan=TAG_ID,
                      in_port=port['ofport']),
            mock.call(icmpv6_type=132,
                      proto=self._write_proto(IPv6, 'icmp'),
                      dl_src=port['mac_address'],
                      actions='resubmit(,%s)' % OF_INGRESS_TABLE,
                      priority=50, table=OF_EGRESS_TABLE, dl_vlan=TAG_ID,
                      in_port=port['ofport']),
            mock.call(priority=10, table=OF_INGRESS_TABLE, dl_vlan=TAG_ID,
                      actions='resubmit(,%s)' % OF_INGRESS_EXT_TABLE),
            mock.call(priority=100, table=OF_INGRESS_EXT_TABLE,
                      dl_dst=port['mac_address'], dl_vlan=TAG_ID,
                      actions='drop'),
            mock.call(priority=100, table=OF_INGRESS_EXT_TABLE,
                      reg0=TAG_ID, dl_vlan=TAG_ID,
                      actions='drop'),
            mock.call(priority=50, table=OF_INGRESS_EXT_TABLE,
                      dl_vlan=TAG_ID, actions='strip_vlan,normal'),
            mock.call(actions='strip_vlan,output:%s' % port['ofport'],
                      proto=self._write_proto(IPv4, 'udp'),
                      priority=50, udp_src=67, udp_dst=68,
                      table=OF_INGRESS_TABLE, dl_vlan=TAG_ID,
                      dl_dst=port['mac_address']),
            mock.call(actions='strip_vlan,output:%s' % port['ofport'],
                      proto=self._write_proto(IPv6, 'udp'),
                      priority=50, udp_src=547, udp_dst=546,
                      table=OF_INGRESS_TABLE, dl_vlan=TAG_ID,
                      dl_dst=port['mac_address']),
            mock.call(actions='strip_vlan,output:%s' % port['ofport'],
                      icmp_type=9,
                      proto=self._write_proto(IPv4, 'icmp'),
                      dl_dst=port['mac_address'], priority=50,
                      table=OF_INGRESS_TABLE, dl_vlan=TAG_ID),
            mock.call(actions='strip_vlan,output:%s' % port['ofport'],
                      icmp_type=10,
                      proto=self._write_proto(IPv4, 'icmp'),
                      dl_dst=port['mac_address'], priority=50,
                      table=OF_INGRESS_TABLE, dl_vlan=TAG_ID),
            mock.call(actions='strip_vlan,output:%s' % port['ofport'],
                      icmpv6_type=130,
                      proto=self._write_proto(IPv6, 'icmp'),
                      dl_dst=port['mac_address'], priority=50,
                      table=OF_INGRESS_TABLE, dl_vlan=TAG_ID),
            mock.call(actions='strip_vlan,output:%s' % port['ofport'],
                      icmpv6_type=131,
                      proto=self._write_proto(IPv6, 'icmp'),
                      dl_dst=port['mac_address'], priority=50,
                      table=OF_INGRESS_TABLE, dl_vlan=TAG_ID),
            mock.call(actions='strip_vlan,output:%s' % port['ofport'],
                      icmpv6_type=132,
                      proto=self._write_proto(IPv6, 'icmp'),
                      dl_dst=port['mac_address'], priority=50,
                      table=OF_INGRESS_TABLE, dl_vlan=TAG_ID),
            mock.call(priority=100, table=OF_INGRESS_EXT_TABLE,
                      dl_vlan=TAG_ID,
                      reg1='1', actions='drop'),
        ]
        self.mock_add_flow.assert_has_calls(calls_add_flows, any_order=True)

    def _test_rules(self, rule_list, fake_sgid, flow_call_list,
                    any_order=False):
        self.firewall.update_security_group_rules(fake_sgid, rule_list)
        self.firewall._add_rules_flows(self.fake_port_1)
        self.mock_add_flow.assert_has_calls(flow_call_list,
                                            any_order=any_order)

    def test_filter_ipv4_ingress(self):
        rule = {'ethertype': IPv4,
                'direction': 'ingress'}
        flow_call_list = []
        for proto in ['tcp', 'udp', 'ip']:
            priority = PROTOCOLS_DEFAULT_PRIO[proto]
            flow_call_list.append(mock.call(
                actions=self._learn_ingress_actions(proto, rule['ethertype'],
                    priority, ofport=self.fake_port_1['ofport']),
                dl_dst=self.fake_port_1['mac_address'],
                nw_dst=FAKE_IP[IPv4],
                priority=30,
                proto=self._write_proto(IPv4, proto),
                table=OF_INGRESS_TABLE))
        self._test_rules([rule], FAKE_SGID, flow_call_list)

    def test_filter_ipv6_ingress(self):
        rule = {'ethertype': IPv6,
                'direction': 'ingress'}
        flow_call_list = []
        for proto in ['tcp', 'udp', 'ipv6']:
            priority = PROTOCOLS_DEFAULT_PRIO[proto]
            flow_call_list.append(mock.call(
                actions=self._learn_ingress_actions(proto, rule['ethertype'],
                    priority, ofport=self.fake_port_1['ofport']),
                dl_dst=self.fake_port_1['mac_address'],
                ipv6_dst=FAKE_IP[IPv6],
                priority=30,
                proto=self._write_proto(IPv6, proto),
                table=OF_INGRESS_TABLE))
        self._test_rules([rule], FAKE_SGID, flow_call_list)

    def test_filter_ipv4_ingress_prefix(self):
        prefix = FAKE_PREFIX[IPv4]
        rule = {'ethertype': IPv4,
                'direction': 'ingress',
                'source_ip_prefix': prefix}
        flow_call_list = []
        for proto in ['tcp', 'udp', 'ip']:
            priority = PROTOCOLS_DEFAULT_PRIO[proto]
            flow_call_list.append(mock.call(
                actions=self._learn_ingress_actions(proto, rule['ethertype'],
                    priority, ofport=self.fake_port_1['ofport']),
                dl_dst=self.fake_port_1['mac_address'],
                nw_dst=FAKE_IP[IPv4],
                nw_src=prefix,
                priority=30,

                proto=self._write_proto(IPv4, proto),
                table=OF_INGRESS_TABLE))
        self._test_rules([rule], FAKE_SGID, flow_call_list)

    def test_filter_ipv6_ingress_prefix(self):
        prefix = FAKE_PREFIX[IPv6]
        rule = {'ethertype': IPv6,
                'direction': 'ingress',
                'source_ip_prefix': prefix}
        flow_call_list = []
        for proto in ['tcp', 'udp', 'ipv6']:
            priority = PROTOCOLS_DEFAULT_PRIO[proto]
            flow_call_list.append(mock.call(
                actions=self._learn_ingress_actions(proto, rule['ethertype'],
                    priority, ofport=self.fake_port_1['ofport']),
                dl_dst=self.fake_port_1['mac_address'],
                ipv6_dst=FAKE_IP[IPv6],
                ipv6_src=prefix,
                priority=30,
                proto=self._write_proto(IPv6, proto),
                table=OF_INGRESS_TABLE))
        self._test_rules([rule], FAKE_SGID, flow_call_list)

    def test_filter_ipv4_ingress_tcp(self):
        proto = 'tcp'
        priority = PROTOCOLS_LEARN_ACTION_PRIO[proto]
        rule = {'ethertype': IPv4,
                'direction': 'ingress',
                'protocol': proto}
        flow_call_list = [mock.call(
            actions=self._learn_ingress_actions(proto, rule['ethertype'],
                priority, ofport=self.fake_port_1['ofport']),
            dl_dst=self.fake_port_1['mac_address'],
            nw_dst=FAKE_IP[IPv4],
            priority=30,
            proto=self._write_proto(IPv4, proto),
            table=OF_INGRESS_TABLE)]
        self._test_rules([rule], FAKE_SGID, flow_call_list)

    def test_filter_ipv6_ingress_tcp(self):
        proto = 'tcp'
        priority = PROTOCOLS_LEARN_ACTION_PRIO[proto]
        rule = {'ethertype': IPv6,
                'direction': 'ingress',
                'protocol': proto}
        flow_call_list = [mock.call(
            actions=self._learn_ingress_actions(proto, rule['ethertype'],
                priority, ofport=self.fake_port_1['ofport']),
            dl_dst=self.fake_port_1['mac_address'],
            ipv6_dst=FAKE_IP[IPv6],
            priority=30,
            proto=self._write_proto(IPv6, proto),
            table=OF_INGRESS_TABLE)]
        self._test_rules([rule], FAKE_SGID, flow_call_list)

    def test_filter_ipv4_ingress_tcp_prefix(self):
        proto = 'tcp'
        priority = PROTOCOLS_LEARN_ACTION_PRIO[proto]
        prefix = FAKE_PREFIX[IPv4]
        rule = {'ethertype': IPv4,
                'direction': 'ingress',
                'protocol': proto,
                'source_ip_prefix': prefix}
        flow_call_list = [mock.call(
            actions=self._learn_ingress_actions(proto, rule['ethertype'],
                priority, ofport=self.fake_port_1['ofport']),
            dl_dst=self.fake_port_1['mac_address'],
            nw_dst=FAKE_IP[IPv4],
            nw_src=prefix,
            priority=30,
            proto=self._write_proto(IPv4, proto),
            table=OF_INGRESS_TABLE)]
        self._test_rules([rule], FAKE_SGID, flow_call_list)

    def test_filter_ipv6_ingress_tcp_prefix(self):
        proto = 'tcp'
        priority = PROTOCOLS_LEARN_ACTION_PRIO[proto]
        prefix = FAKE_PREFIX[IPv6]
        rule = {'ethertype': IPv6,
                'direction': 'ingress',
                'protocol': proto,
                'source_ip_prefix': prefix}
        flow_call_list = [mock.call(
            actions=self._learn_ingress_actions(proto, rule['ethertype'],
                priority, ofport=self.fake_port_1['ofport']),
            dl_dst=self.fake_port_1['mac_address'],
            ipv6_dst=FAKE_IP[IPv6],
            ipv6_src=prefix,
            priority=30,
            proto=self._write_proto(IPv6, proto),
            table=OF_INGRESS_TABLE)]
        self._test_rules([rule], FAKE_SGID, flow_call_list)

    def test_filter_ipv4_ingress_icmp(self):
        proto = 'icmp'
        priority = PROTOCOLS_LEARN_ACTION_PRIO[proto]
        icmp_type = 10
        icmp_code = 20
        prefix = FAKE_PREFIX[IPv4]
        rule = {'ethertype': IPv4,
                'direction': 'ingress',
                'protocol': proto,
                'port_range_min': icmp_type,
                'port_range_max': icmp_code,
                'source_ip_prefix': prefix}
        flow_call_list = [mock.call(
            actions=self._learn_ingress_actions(proto, rule['ethertype'],
                priority, ofport=self.fake_port_1['ofport'],
                icmp_type=icmp_type, icmp_code=icmp_code),
            dl_dst=self.fake_port_1['mac_address'],
            nw_dst=FAKE_IP[IPv4],
            nw_src=prefix,
            priority=30,
            proto=self._write_proto(IPv4, proto),
            table=OF_INGRESS_TABLE)]
        self._test_rules([rule], FAKE_SGID, flow_call_list)

    def test_filter_ipv6_ingress_icmp(self):
        proto = 'icmp'
        priority = PROTOCOLS_LEARN_ACTION_PRIO[proto]
        icmp_type = 10
        icmp_code = 20
        prefix = FAKE_PREFIX[IPv6]
        rule = {'ethertype': IPv6,
                'direction': 'ingress',
                'protocol': proto,
                'port_range_min': icmp_type,
                'port_range_max': icmp_code,
                'source_ip_prefix': prefix}
        flow_call_list = [mock.call(
            actions=self._learn_ingress_actions(proto, rule['ethertype'],
                priority, ofport=self.fake_port_1['ofport'],
                icmp_type=icmp_type, icmp_code=icmp_code),
            dl_dst=self.fake_port_1['mac_address'],
            ipv6_dst=FAKE_IP[IPv6],
            ipv6_src=prefix,
            priority=30,
            proto=self._write_proto(IPv6, proto),
            table=OF_INGRESS_TABLE)]
        self._test_rules([rule], FAKE_SGID, flow_call_list)

    def test_filter_ipv4_ingress_icmp_prefix(self):
        proto = 'icmp'
        priority = PROTOCOLS_LEARN_ACTION_PRIO[proto]
        icmp_type = 10
        icmp_code = 20
        prefix = FAKE_PREFIX[IPv4]
        rule = {'ethertype': IPv4,
                'direction': 'ingress',
                'protocol': proto,
                'port_range_min': icmp_type,
                'port_range_max': icmp_code,
                'source_ip_prefix': prefix}
        flow_call_list = [mock.call(
            actions=self._learn_ingress_actions(proto, rule['ethertype'],
                priority, ofport=self.fake_port_1['ofport'],
                icmp_type=icmp_type, icmp_code=icmp_code),
            dl_dst=self.fake_port_1['mac_address'],
            nw_dst=FAKE_IP[IPv4],
            nw_src=prefix,
            priority=30,
            proto=self._write_proto(IPv4, proto),
            table=OF_INGRESS_TABLE)]
        self._test_rules([rule], FAKE_SGID, flow_call_list)

    def test_filter_ipv6_ingress_icmp_prefix(self):
        proto = 'icmp'
        priority = PROTOCOLS_LEARN_ACTION_PRIO[proto]
        icmp_type = 10
        icmp_code = 20
        prefix = FAKE_PREFIX[IPv6]
        rule = {'ethertype': IPv6,
                'direction': 'ingress',
                'protocol': proto,
                'port_range_min': icmp_type,
                'port_range_max': icmp_code,
                'source_ip_prefix': prefix}
        flow_call_list = [mock.call(
            actions=self._learn_ingress_actions(proto, rule['ethertype'],
                priority, ofport=self.fake_port_1['ofport'],
                icmp_type=icmp_type, icmp_code=icmp_code),
            dl_dst=self.fake_port_1['mac_address'],
            ipv6_dst=FAKE_IP[IPv6],
            ipv6_src=prefix,
            priority=30,
            proto=self._write_proto(IPv6, proto),
            table=OF_INGRESS_TABLE)]
        self._test_rules([rule], FAKE_SGID, flow_call_list)

    def test_filter_ipv4_ingress_tcp_port(self):
        proto = 'tcp'
        priority = PROTOCOLS_LEARN_ACTION_PRIO[proto]
        rule = {'ethertype': IPv4,
                'direction': 'ingress',
                'protocol': proto,
                'port_range_min': 10,
                'port_range_max': 10}
        flow_call_list = [mock.call(
            actions=self._learn_ingress_actions(proto, rule['ethertype'],
                priority, ofport=self.fake_port_1['ofport']),
            dl_dst=self.fake_port_1['mac_address'],
            nw_dst=FAKE_IP[IPv4],
            tcp_dst=rule['port_range_min'],
            priority=30,
            proto=self._write_proto(IPv4, proto),
            table=OF_INGRESS_TABLE)]
        self._test_rules([rule], FAKE_SGID, flow_call_list)

    def test_filter_ipv6_ingress_tcp_port(self):
        proto = 'tcp'
        priority = PROTOCOLS_LEARN_ACTION_PRIO[proto]
        rule = {'ethertype': IPv6,
                'direction': 'ingress',
                'protocol': proto,
                'port_range_min': 10,
                'port_range_max': 10}
        flow_call_list = [mock.call(
            actions=self._learn_ingress_actions(proto, rule['ethertype'],
                priority, ofport=self.fake_port_1['ofport']),
            dl_dst=self.fake_port_1['mac_address'],
            ipv6_dst=FAKE_IP[IPv6],
            tcp_dst=rule['port_range_min'],
            priority=30,
            proto=self._write_proto(IPv6, proto),
            table=OF_INGRESS_TABLE)]
        self._test_rules([rule], FAKE_SGID, flow_call_list)

    def test_filter_ipv4_ingress_tcp_mport(self):
        proto = 'tcp'
        priority = PROTOCOLS_LEARN_ACTION_PRIO[proto]
        rule = {'ethertype': IPv4,
                'direction': 'ingress',
                'protocol': proto,
                'port_range_min': 10,
                'port_range_max': 100}
        flow_call_list = []
        for port in self.mport:
            flow_call_list.append(mock.call(
                actions=self._learn_ingress_actions(proto, rule['ethertype'],
                    priority, ofport=self.fake_port_1['ofport']),
                dl_dst=self.fake_port_1['mac_address'],
                nw_dst=FAKE_IP[IPv4],
                tcp_dst=port,
                priority=30,
                proto=self._write_proto(IPv4, proto),
                table=OF_INGRESS_TABLE))
        self._test_rules([rule], FAKE_SGID, flow_call_list, any_order=True)

    def test_filter_ipv6_ingress_tcp_mport(self):
        proto = 'tcp'
        priority = PROTOCOLS_LEARN_ACTION_PRIO[proto]
        rule = {'ethertype': IPv6,
                'direction': 'ingress',
                'protocol': proto,
                'port_range_min': 10,
                'port_range_max': 100}
        flow_call_list = []
        for port in self.mport:
            flow_call_list.append(mock.call(
                actions=self._learn_ingress_actions(proto, rule['ethertype'],
                    priority, ofport=self.fake_port_1['ofport']),
                dl_dst=self.fake_port_1['mac_address'],
                ipv6_dst=FAKE_IP[IPv6],
                tcp_dst=port,
                priority=30,
                proto=self._write_proto(IPv6, proto),
                table=OF_INGRESS_TABLE))
        self._test_rules([rule], FAKE_SGID, flow_call_list, any_order=True)

    def test_filter_ipv4_ingress_tcp_mport_prefix(self):
        proto = 'tcp'
        priority = PROTOCOLS_LEARN_ACTION_PRIO[proto]
        prefix = FAKE_PREFIX[IPv4]
        rule = {'ethertype': IPv4,
                'direction': 'ingress',
                'protocol': 'tcp',
                'port_range_min': 10,
                'port_range_max': 100,
                'source_ip_prefix': prefix}
        flow_call_list = []
        for port in self.mport:
            flow_call_list.append(mock.call(
                actions=self._learn_ingress_actions(proto, rule['ethertype'],
                    priority, ofport=self.fake_port_1['ofport']),
                dl_dst=self.fake_port_1['mac_address'],
                nw_dst=FAKE_IP[IPv4],
                nw_src=prefix,
                tcp_dst=port,
                priority=30,
                proto=self._write_proto(IPv4, proto),
                table=OF_INGRESS_TABLE))
        self._test_rules([rule], FAKE_SGID, flow_call_list, any_order=True)

    def test_filter_ipv6_ingress_tcp_mport_prefix(self):
        proto = 'tcp'
        priority = PROTOCOLS_LEARN_ACTION_PRIO[proto]
        prefix = FAKE_PREFIX[IPv6]
        rule = {'ethertype': IPv6,
                'direction': 'ingress',
                'protocol': 'tcp',
                'port_range_min': 10,
                'port_range_max': 100,
                'source_ip_prefix': prefix}
        flow_call_list = []
        for port in self.mport:
            flow_call_list.append(mock.call(
                actions=self._learn_ingress_actions(proto, rule['ethertype'],
                    priority, ofport=self.fake_port_1['ofport']),
                dl_dst=self.fake_port_1['mac_address'],
                ipv6_dst=FAKE_IP[IPv6],
                ipv6_src=prefix,
                tcp_dst=port,
                priority=30,
                proto=self._write_proto(IPv6, proto),
                table=OF_INGRESS_TABLE))
        self._test_rules([rule], FAKE_SGID, flow_call_list, any_order=True)

    def test_filter_ipv4_ingress_udp(self):
        proto = 'udp'
        priority = PROTOCOLS_LEARN_ACTION_PRIO[proto]
        rule = {'ethertype': IPv4,
                'direction': 'ingress',
                'protocol': proto}
        flow_call_list = [mock.call(
            actions=self._learn_ingress_actions(proto, rule['ethertype'],
                priority, ofport=self.fake_port_1['ofport']),
            dl_dst=self.fake_port_1['mac_address'],
            nw_dst=FAKE_IP[IPv4],
            priority=30,
            proto=self._write_proto(IPv4, proto),
            table=OF_INGRESS_TABLE)]
        self._test_rules([rule], FAKE_SGID, flow_call_list)

    def test_filter_ipv6_ingress_udp(self):
        proto = 'udp'
        priority = PROTOCOLS_LEARN_ACTION_PRIO[proto]
        rule = {'ethertype': IPv6,
                'direction': 'ingress',
                'protocol': proto}
        flow_call_list = [mock.call(
            actions=self._learn_ingress_actions(proto, rule['ethertype'],
                priority, ofport=self.fake_port_1['ofport']),
            dl_dst=self.fake_port_1['mac_address'],
            ipv6_dst=FAKE_IP[IPv6],
            priority=30,
            proto=self._write_proto(IPv6, proto),
            table=OF_INGRESS_TABLE)]
        self._test_rules([rule], FAKE_SGID, flow_call_list)

    def test_filter_ipv4_ingress_udp_prefix(self):
        proto = 'udp'
        priority = PROTOCOLS_LEARN_ACTION_PRIO[proto]
        prefix = FAKE_PREFIX[IPv4]
        rule = {'ethertype': IPv4,
                'direction': 'ingress',
                'protocol': proto,
                'source_ip_prefix': prefix}
        flow_call_list = [mock.call(
            actions=self._learn_ingress_actions(proto, rule['ethertype'],
                priority, ofport=self.fake_port_1['ofport']),
            dl_dst=self.fake_port_1['mac_address'],
            nw_dst=FAKE_IP[IPv4],
            nw_src=prefix,
            priority=30,
            proto=self._write_proto(IPv4, proto),
            table=OF_INGRESS_TABLE)]
        self._test_rules([rule], FAKE_SGID, flow_call_list)

    def test_filter_ipv6_ingress_udp_prefix(self):
        proto = 'udp'
        priority = PROTOCOLS_LEARN_ACTION_PRIO[proto]
        prefix = FAKE_PREFIX[IPv6]
        rule = {'ethertype': IPv6,
                'direction': 'ingress',
                'protocol': proto,
                'source_ip_prefix': prefix}
        flow_call_list = [mock.call(
            actions=self._learn_ingress_actions(proto, rule['ethertype'],
                priority, ofport=self.fake_port_1['ofport']),
            dl_dst=self.fake_port_1['mac_address'],
            ipv6_dst=FAKE_IP[IPv6],
            ipv6_src=prefix,
            priority=30,
            proto=self._write_proto(IPv6, proto),
            table=OF_INGRESS_TABLE)]
        self._test_rules([rule], FAKE_SGID, flow_call_list)

    def test_filter_ipv4_ingress_udp_port(self):
        proto = 'udp'
        priority = PROTOCOLS_LEARN_ACTION_PRIO[proto]
        rule = {'ethertype': IPv4,
                'direction': 'ingress',
                'protocol': 'udp',
                'port_range_min': 10,
                'port_range_max': 10}
        flow_call_list = [mock.call(
            actions=self._learn_ingress_actions(proto, rule['ethertype'],
                priority, ofport=self.fake_port_1['ofport']),
            dl_dst=self.fake_port_1['mac_address'],
            nw_dst=FAKE_IP[IPv4],
            priority=30,
            udp_dst=10,
            proto=self._write_proto(IPv4, proto),
            table=OF_INGRESS_TABLE)]
        self._test_rules([rule], FAKE_SGID, flow_call_list)

    def test_filter_ipv6_ingress_udp_port(self):
        proto = 'udp'
        priority = PROTOCOLS_LEARN_ACTION_PRIO[proto]
        rule = {'ethertype': IPv6,
                'direction': 'ingress',
                'protocol': 'udp',
                'port_range_min': 10,
                'port_range_max': 10}
        flow_call_list = [mock.call(
            actions=self._learn_ingress_actions(proto, rule['ethertype'],
                priority, ofport=self.fake_port_1['ofport']),
            dl_dst=self.fake_port_1['mac_address'],
            ipv6_dst=FAKE_IP[IPv6],
            priority=30,
            udp_dst=10,
            proto=self._write_proto(IPv6, proto),
            table=OF_INGRESS_TABLE)]
        self._test_rules([rule], FAKE_SGID, flow_call_list)

    def test_filter_ipv4_ingress_udp_mport(self):
        proto = 'udp'
        priority = PROTOCOLS_LEARN_ACTION_PRIO[proto]
        rule = {'ethertype': IPv4,
                'direction': 'ingress',
                'protocol': 'udp',
                'port_range_min': 10,
                'port_range_max': 100}
        flow_call_list = []
        for port in self.mport:
            flow_call_list.append(mock.call(
                actions=self._learn_ingress_actions(proto, rule['ethertype'],
                    priority, ofport=self.fake_port_1['ofport']),
                dl_dst=self.fake_port_1['mac_address'],
                nw_dst=FAKE_IP[IPv4],
                udp_dst=port,
                priority=30,
                proto=self._write_proto(IPv4, proto),
                table=OF_INGRESS_TABLE))
        self._test_rules([rule], FAKE_SGID, flow_call_list)

    def test_filter_ipv6_ingress_udp_mport(self):
        proto = 'udp'
        priority = PROTOCOLS_LEARN_ACTION_PRIO[proto]
        rule = {'ethertype': IPv6,
                'direction': 'ingress',
                'protocol': 'udp',
                'port_range_min': 10,
                'port_range_max': 100}
        flow_call_list = []
        for port in self.mport:
            flow_call_list.append(mock.call(
                actions=self._learn_ingress_actions(proto, rule['ethertype'],
                    priority, ofport=self.fake_port_1['ofport']),
                dl_dst=self.fake_port_1['mac_address'],
                ipv6_dst=FAKE_IP[IPv6],
                udp_dst=port,
                priority=30,
                proto=self._write_proto(IPv6, proto),
                table=OF_INGRESS_TABLE))
        self._test_rules([rule], FAKE_SGID, flow_call_list, any_order=True)

    def test_filter_ipv4_ingress_udp_mport_prefix(self):
        proto = 'udp'
        priority = PROTOCOLS_LEARN_ACTION_PRIO[proto]
        prefix = FAKE_PREFIX[IPv4]
        rule = {'ethertype': IPv4,
                'direction': 'ingress',
                'protocol': 'udp',
                'port_range_min': 10,
                'port_range_max': 100,
                'source_ip_prefix': prefix}
        flow_call_list = []
        for port in self.mport:
            flow_call_list.append(mock.call(
                actions=self._learn_ingress_actions(proto, rule['ethertype'],
                    priority, ofport=self.fake_port_1['ofport']),
                dl_dst=self.fake_port_1['mac_address'],
                nw_dst=FAKE_IP[IPv4],
                nw_src=prefix,
                udp_dst=port,
                priority=30,
                proto=self._write_proto(IPv4, proto),
                table=OF_INGRESS_TABLE))
        self._test_rules([rule], FAKE_SGID, flow_call_list, any_order=True)

    def test_filter_ipv6_ingress_udp_mport_prefix(self):
        proto = 'udp'
        priority = PROTOCOLS_LEARN_ACTION_PRIO[proto]
        prefix = FAKE_PREFIX[IPv6]
        rule = {'ethertype': IPv6,
                'direction': 'ingress',
                'protocol': 'udp',
                'port_range_min': 10,
                'port_range_max': 100,
                'source_ip_prefix': prefix}
        flow_call_list = []
        for port in self.mport:
            flow_call_list.append(mock.call(
                actions=self._learn_ingress_actions(proto, rule['ethertype'],
                    priority, ofport=self.fake_port_1['ofport']),
                dl_dst=self.fake_port_1['mac_address'],
                ipv6_dst=FAKE_IP[IPv6],
                ipv6_src=prefix,
                udp_dst=port,
                priority=30,
                proto=self._write_proto(IPv6, proto),
                table=OF_INGRESS_TABLE))
        self._test_rules([rule], FAKE_SGID, flow_call_list, any_order=True)

    def test_filter_ipv4_egress(self):
        rule = {'ethertype': IPv4,
                'direction': 'egress'}
        flow_call_list = []
        for proto in ['tcp', 'udp', 'ip']:
            priority = PROTOCOLS_DEFAULT_PRIO[proto]
            flow_call_list.append(
                mock.call(actions=self._learn_egress_actions(proto,
                                rule['ethertype'], priority),
                          dl_src=self.fake_port_1['mac_address'],
                          nw_src=FAKE_IP[IPv4],
                          priority=30,
                          proto=self._write_proto(IPv4, proto),
                          table=OF_EGRESS_TABLE))
        self._test_rules([rule], FAKE_SGID, flow_call_list)

    def test_filter_ipv6_egress(self):
        rule = {'ethertype': IPv6,
                'direction': 'egress'}
        flow_call_list = []
        for proto in ['tcp', 'udp', 'ipv6']:
            priority = PROTOCOLS_DEFAULT_PRIO[proto]
            flow_call_list.append(
                mock.call(actions=self._learn_egress_actions(proto,
                                rule['ethertype'], priority),
                          dl_src=self.fake_port_1['mac_address'],
                          ipv6_src=FAKE_IP[IPv6],
                          priority=30,
                          proto=self._write_proto(IPv6, proto),
                          table=OF_EGRESS_TABLE))
        self._test_rules([rule], FAKE_SGID, flow_call_list)

    def test_filter_ipv4_egress_prefix(self):
        prefix = FAKE_PREFIX[IPv4]
        rule = {'ethertype': IPv4,
                'direction': 'egress',
                'dest_ip_prefix': prefix}
        flow_call_list = []
        for proto in ['tcp', 'udp', 'ip']:
            priority = PROTOCOLS_DEFAULT_PRIO[proto]
            flow_call_list.append(
                mock.call(actions=self._learn_egress_actions(proto,
                                rule['ethertype'], priority),
                          dl_src=self.fake_port_1['mac_address'],
                          nw_src=FAKE_IP[IPv4],
                          nw_dst=prefix,
                          priority=30,
                          proto=self._write_proto(IPv4, proto),
                          table=OF_EGRESS_TABLE))
        self._test_rules([rule], FAKE_SGID, flow_call_list)

    def test_filter_ipv6_egress_prefix(self):
        prefix = FAKE_PREFIX[IPv6]
        rule = {'ethertype': IPv6,
                'direction': 'egress',
                'dest_ip_prefix': prefix}
        flow_call_list = []
        for proto in ['tcp', 'udp', 'ipv6']:
            priority = PROTOCOLS_DEFAULT_PRIO[proto]
            flow_call_list.append(
                mock.call(actions=self._learn_egress_actions(proto,
                                rule['ethertype'], priority),
                          dl_src=self.fake_port_1['mac_address'],
                          ipv6_src=FAKE_IP[IPv6],
                          ipv6_dst=prefix,
                          priority=30,
                          proto=self._write_proto(IPv6, proto),
                          table=OF_EGRESS_TABLE))
        self._test_rules([rule], FAKE_SGID, flow_call_list)

    def test_filter_ipv4_egress_tcp(self):
        proto = 'tcp'
        priority = PROTOCOLS_LEARN_ACTION_PRIO[proto]
        rule = {'ethertype': IPv4,
                'direction': 'egress',
                'protocol': proto}
        flow_call_list = [mock.call(actions=self._learn_egress_actions(proto,
                                        rule['ethertype'], priority),
                                    dl_src=self.fake_port_1['mac_address'],
                                    nw_src=FAKE_IP[IPv4],
                                    priority=30,
                                    proto=self._write_proto(IPv4,
                                                            proto),
                                    table=OF_EGRESS_TABLE)]
        self._test_rules([rule], FAKE_SGID, flow_call_list)

    def test_filter_ipv6_egress_tcp(self):
        proto = 'tcp'
        priority = PROTOCOLS_LEARN_ACTION_PRIO[proto]
        rule = {'ethertype': IPv6,
                'direction': 'egress',
                'protocol': proto}
        flow_call_list = [mock.call(actions=self._learn_egress_actions(proto,
                                        rule['ethertype'], priority),
                                    dl_src=self.fake_port_1['mac_address'],
                                    ipv6_src=FAKE_IP[IPv6],
                                    priority=30,
                                    proto=self._write_proto(IPv6,
                                                            proto),
                                    table=OF_EGRESS_TABLE)]
        self._test_rules([rule], FAKE_SGID, flow_call_list)

    def test_filter_ipv4_egress_tcp_prefix(self):
        proto = 'tcp'
        priority = PROTOCOLS_LEARN_ACTION_PRIO[proto]
        prefix = FAKE_PREFIX[IPv4]
        rule = {'ethertype': IPv4,
                'direction': 'egress',
                'protocol': proto,
                'dest_ip_prefix': prefix}
        flow_call_list = [mock.call(actions=self._learn_egress_actions(proto,
                                        rule['ethertype'], priority),
                                    dl_src=self.fake_port_1['mac_address'],
                                    nw_dst=prefix,
                                    nw_src=FAKE_IP[IPv4],
                                    priority=30,
                                    proto=self._write_proto(IPv4,
                                                            proto),
                                    table=OF_EGRESS_TABLE)]
        self._test_rules([rule], FAKE_SGID, flow_call_list)

    def test_filter_ipv6_egress_tcp_prefix(self):
        proto = 'tcp'
        priority = PROTOCOLS_LEARN_ACTION_PRIO[proto]
        prefix = FAKE_PREFIX[IPv6]
        rule = {'ethertype': IPv6,
                'direction': 'egress',
                'protocol': proto,
                'dest_ip_prefix': prefix}
        flow_call_list = [mock.call(actions=self._learn_egress_actions(proto,
                                        rule['ethertype'], priority),
                                    dl_src=self.fake_port_1['mac_address'],
                                    ipv6_dst=prefix,
                                    ipv6_src=FAKE_IP[IPv6],
                                    priority=30,
                                    proto=self._write_proto(IPv6,
                                                            proto),
                                    table=OF_EGRESS_TABLE)]
        self._test_rules([rule], FAKE_SGID, flow_call_list)

    def test_filter_ipv4_egress_icmp(self):
        proto = 'icmp'
        priority = PROTOCOLS_LEARN_ACTION_PRIO[proto]
        icmp_type = 10
        icmp_code = 20
        rule = {'ethertype': IPv4,
                'direction': 'egress',
                'protocol': proto,
                'port_range_min': icmp_type,
                'port_range_max': icmp_code}
        flow_call_list = [mock.call(actions=self._learn_egress_actions(proto,
                                        rule['ethertype'], priority,
                                        icmp_type=icmp_type,
                                        icmp_code=icmp_code),
                                    dl_src=self.fake_port_1['mac_address'],
                                    nw_src=FAKE_IP[IPv4],
                                    priority=30,
                                    proto=self._write_proto(IPv4,
                                                            proto),
                                    table=OF_EGRESS_TABLE)]
        self._test_rules([rule], FAKE_SGID, flow_call_list)

    def test_filter_ipv6_egress_icmp(self):
        proto = 'icmp'
        priority = PROTOCOLS_LEARN_ACTION_PRIO[proto]
        icmp_type = 10
        icmp_code = 20
        rule = {'ethertype': IPv6,
                'direction': 'egress',
                'protocol': proto,
                'port_range_min': icmp_type,
                'port_range_max': icmp_code}
        flow_call_list = [mock.call(actions=self._learn_egress_actions(proto,
                                        rule['ethertype'], priority,
                                        icmp_type=icmp_type,
                                        icmp_code=icmp_code),
                                    dl_src=self.fake_port_1['mac_address'],
                                    ipv6_src=FAKE_IP[IPv6],
                                    priority=30,
                                    proto=self._write_proto(IPv6,
                                                            proto),
                                    table=OF_EGRESS_TABLE)]
        self._test_rules([rule], FAKE_SGID, flow_call_list)

    def test_filter_ipv4_egress_icmp_prefix(self):
        proto = 'icmp'
        priority = PROTOCOLS_LEARN_ACTION_PRIO[proto]
        icmp_type = 10
        icmp_code = 20
        prefix = FAKE_PREFIX[IPv4]
        rule = {'ethertype': IPv4,
                'direction': 'egress',
                'protocol': 'icmp',
                'dest_ip_prefix': prefix,
                'port_range_min': icmp_type,
                'port_range_max': icmp_code}
        flow_call_list = [mock.call(actions=self._learn_egress_actions(proto,
                                        rule['ethertype'], priority,
                                        icmp_type=icmp_type,
                                        icmp_code=icmp_code),
                                    dl_src=self.fake_port_1['mac_address'],
                                    nw_dst=prefix,
                                    nw_src=FAKE_IP[IPv4],
                                    priority=30,
                                    proto=self._write_proto(IPv4,
                                                            proto),
                                    table=OF_EGRESS_TABLE)]
        self._test_rules([rule], FAKE_SGID, flow_call_list)

    def test_filter_ipv6_egress_icmp_prefix(self):
        proto = 'icmp'
        priority = PROTOCOLS_LEARN_ACTION_PRIO[proto]
        icmp_type = 10
        icmp_code = 20
        prefix = FAKE_PREFIX[IPv6]
        rule = {'ethertype': IPv6,
                'direction': 'egress',
                'protocol': 'icmp',
                'dest_ip_prefix': prefix,
                'port_range_min': icmp_type,
                'port_range_max': icmp_code}
        flow_call_list = [mock.call(actions=self._learn_egress_actions(proto,
                                        rule['ethertype'], priority,
                                        icmp_type=icmp_type,
                                        icmp_code=icmp_code),
                                    dl_src=self.fake_port_1['mac_address'],
                                    ipv6_dst=prefix,
                                    ipv6_src=FAKE_IP[IPv6],
                                    priority=30,
                                    proto=self._write_proto(IPv6,
                                                            proto),
                                    table=OF_EGRESS_TABLE)]
        self._test_rules([rule], FAKE_SGID, flow_call_list)

    def test_filter_ipv4_egress_tcp_port(self):
        proto = 'tcp'
        priority = PROTOCOLS_LEARN_ACTION_PRIO[proto]
        rule = {'ethertype': IPv4,
                'direction': 'egress',
                'protocol': proto,
                'port_range_min': 10,
                'port_range_max': 10}
        flow_call_list = [mock.call(actions=self._learn_egress_actions(proto,
                                        rule['ethertype'], priority),
                                    dl_src=self.fake_port_1['mac_address'],
                                    nw_src=FAKE_IP[IPv4],
                                    priority=30,
                                    proto=self._write_proto(IPv4,
                                                            proto),
                                    table=OF_EGRESS_TABLE,
                                    tcp_dst=rule['port_range_min'])]
        self._test_rules([rule], FAKE_SGID, flow_call_list)

    def test_filter_ipv6_egress_tcp_port(self):
        proto = 'tcp'
        priority = PROTOCOLS_LEARN_ACTION_PRIO[proto]
        rule = {'ethertype': IPv6,
                'direction': 'egress',
                'protocol': proto,
                'port_range_min': 10,
                'port_range_max': 10}
        flow_call_list = [mock.call(actions=self._learn_egress_actions(proto,
                                        rule['ethertype'], priority),
                                    dl_src=self.fake_port_1['mac_address'],
                                    ipv6_src=FAKE_IP[IPv6],
                                    priority=30,
                                    proto=self._write_proto(IPv6,
                                                            proto),
                                    table=OF_EGRESS_TABLE,
                                    tcp_dst=rule['port_range_min'])]
        self._test_rules([rule], FAKE_SGID, flow_call_list)

    def test_filter_ipv4_egress_tcp_mport(self):
        proto = 'tcp'
        priority = PROTOCOLS_LEARN_ACTION_PRIO[proto]
        rule = {'ethertype': IPv4,
                'direction': 'egress',
                'protocol': proto,
                'port_range_min': 10,
                'port_range_max': 100}
        flow_call_list = []
        for port in self.mport:
            flow_call_list.append(
                mock.call(actions=self._learn_egress_actions(proto,
                                rule['ethertype'], priority),
                          dl_src=self.fake_port_1['mac_address'],
                          nw_src=FAKE_IP[IPv4],
                          priority=30,
                          proto=self._write_proto(IPv4, proto),
                          table=OF_EGRESS_TABLE,
                          tcp_dst=port))
        self._test_rules([rule], FAKE_SGID, flow_call_list, any_order=True)

    def test_filter_ipv6_egress_tcp_mport(self):
        proto = 'tcp'
        priority = PROTOCOLS_LEARN_ACTION_PRIO[proto]
        rule = {'ethertype': IPv6,
                'direction': 'egress',
                'protocol': proto,
                'port_range_min': 10,
                'port_range_max': 100}
        flow_call_list = []
        for port in self.mport:
            flow_call_list.append(
                mock.call(actions=self._learn_egress_actions(proto,
                                rule['ethertype'], priority),
                          dl_src=self.fake_port_1['mac_address'],
                          ipv6_src=FAKE_IP[IPv6],
                          priority=30,
                          proto=self._write_proto(IPv6, proto),
                          table=OF_EGRESS_TABLE,
                          tcp_dst=port))
        self._test_rules([rule], FAKE_SGID, flow_call_list, any_order=True)

    def test_filter_ipv4_egress_tcp_mport_prefix(self):
        proto = 'tcp'
        priority = PROTOCOLS_LEARN_ACTION_PRIO[proto]
        prefix = FAKE_PREFIX[IPv4]
        rule = {'ethertype': IPv4,
                'direction': 'egress',
                'protocol': 'tcp',
                'port_range_min': 10,
                'port_range_max': 100,
                'dest_ip_prefix': prefix}
        flow_call_list = []
        for port in self.mport:
            flow_call_list.append(
                mock.call(actions=self._learn_egress_actions(proto,
                            rule['ethertype'], priority),
                          dl_src=self.fake_port_1['mac_address'],
                          nw_dst=prefix,
                          nw_src=FAKE_IP[IPv4],
                          priority=30,
                          proto=self._write_proto(IPv4, proto),
                          table=OF_EGRESS_TABLE,
                          tcp_dst=port))
        self._test_rules([rule], FAKE_SGID, flow_call_list, any_order=True)

    def test_filter_ipv6_egress_tcp_mport_prefix(self):
        proto = 'tcp'
        priority = PROTOCOLS_LEARN_ACTION_PRIO[proto]
        prefix = FAKE_PREFIX[IPv6]
        rule = {'ethertype': IPv6,
                'direction': 'egress',
                'protocol': 'tcp',
                'port_range_min': 10,
                'port_range_max': 100,
                'dest_ip_prefix': prefix}
        flow_call_list = []
        for port in self.mport:
            flow_call_list.append(
                mock.call(actions=self._learn_egress_actions(proto,
                            rule['ethertype'], priority),
                          dl_src=self.fake_port_1['mac_address'],
                          ipv6_dst=prefix,
                          ipv6_src=FAKE_IP[IPv6],
                          priority=30,
                          proto=self._write_proto(IPv6, proto),
                          table=OF_EGRESS_TABLE,
                          tcp_dst=port))
        self._test_rules([rule], FAKE_SGID, flow_call_list, any_order=True)

    def test_filter_ipv4_egress_udp(self):
        proto = 'udp'
        priority = PROTOCOLS_LEARN_ACTION_PRIO[proto]
        rule = {'ethertype': IPv4,
                'direction': 'egress',
                'protocol': proto}
        flow_call_list = [mock.call(actions=self._learn_egress_actions(proto,
                                        rule['ethertype'], priority),
                                    dl_src=self.fake_port_1['mac_address'],
                                    nw_src=FAKE_IP[IPv4],
                                    priority=30,
                                    proto=self._write_proto(IPv4,
                                                            proto),
                                    table=OF_EGRESS_TABLE)]
        self._test_rules([rule], FAKE_SGID, flow_call_list)

    def test_filter_ipv6_egress_udp(self):
        proto = 'udp'
        priority = PROTOCOLS_LEARN_ACTION_PRIO[proto]
        rule = {'ethertype': IPv6,
                'direction': 'egress',
                'protocol': proto}
        flow_call_list = [mock.call(actions=self._learn_egress_actions(proto,
                                        rule['ethertype'], priority),
                                    dl_src=self.fake_port_1['mac_address'],
                                    ipv6_src=FAKE_IP[IPv6],
                                    priority=30,
                                    proto=self._write_proto(IPv6,
                                                            proto),
                                    table=OF_EGRESS_TABLE)]
        self._test_rules([rule], FAKE_SGID, flow_call_list)

    def test_filter_ipv4_egress_udp_prefix(self):
        proto = 'udp'
        priority = PROTOCOLS_LEARN_ACTION_PRIO[proto]
        prefix = FAKE_PREFIX[IPv4]
        rule = {'ethertype': IPv4,
                'direction': 'egress',
                'protocol': proto,
                'dest_ip_prefix': prefix}
        flow_call_list = [mock.call(actions=self._learn_egress_actions(proto,
                                        rule['ethertype'], priority),
                                    dl_src=self.fake_port_1['mac_address'],
                                    nw_dst=prefix,
                                    nw_src=FAKE_IP[IPv4],
                                    priority=30,
                                    proto=self._write_proto(IPv4,
                                                            proto),
                                    table=OF_EGRESS_TABLE)]
        self._test_rules([rule], FAKE_SGID, flow_call_list)

    def test_filter_ipv6_egress_udp_prefix(self):
        proto = 'udp'
        priority = PROTOCOLS_LEARN_ACTION_PRIO[proto]
        prefix = FAKE_PREFIX[IPv6]
        rule = {'ethertype': IPv6,
                'direction': 'egress',
                'protocol': proto,
                'dest_ip_prefix': prefix}
        flow_call_list = [mock.call(actions=self._learn_egress_actions(proto,
                                        rule['ethertype'], priority),
                                    dl_src=self.fake_port_1['mac_address'],
                                    ipv6_dst=prefix,
                                    ipv6_src=FAKE_IP[IPv6],
                                    priority=30,
                                    proto=self._write_proto(IPv6,
                                                            proto),
                                    table=OF_EGRESS_TABLE)]
        self._test_rules([rule], FAKE_SGID, flow_call_list)

    def test_filter_ipv4_egress_udp_port(self):
        proto = 'udp'
        priority = PROTOCOLS_LEARN_ACTION_PRIO[proto]
        rule = {'ethertype': IPv4,
                'direction': 'egress',
                'protocol': proto,
                'port_range_min': 10,
                'port_range_max': 10}
        flow_call_list = [mock.call(actions=self._learn_egress_actions(proto,
                                        rule['ethertype'], priority),
                                    dl_src=self.fake_port_1['mac_address'],
                                    nw_src=FAKE_IP[IPv4],
                                    priority=30,
                                    proto=self._write_proto(IPv4,
                                                            proto),
                                    table=OF_EGRESS_TABLE,
                                    udp_dst=rule['port_range_min'])]
        self._test_rules([rule], FAKE_SGID, flow_call_list)

    def test_filter_ipv6_egress_udp_port(self):
        proto = 'udp'
        priority = PROTOCOLS_LEARN_ACTION_PRIO[proto]
        rule = {'ethertype': IPv6,
                'direction': 'egress',
                'protocol': proto,
                'port_range_min': 10,
                'port_range_max': 10}
        flow_call_list = [mock.call(actions=self._learn_egress_actions(proto,
                                        rule['ethertype'], priority),
                                    dl_src=self.fake_port_1['mac_address'],
                                    ipv6_src=FAKE_IP[IPv6],
                                    priority=30,
                                    proto=self._write_proto(IPv6,
                                                            proto),
                                    table=OF_EGRESS_TABLE,
                                    udp_dst=rule['port_range_min'])]
        self._test_rules([rule], FAKE_SGID, flow_call_list)

    def test_filter_ipv4_egress_udp_mport(self):
        proto = 'udp'
        priority = PROTOCOLS_LEARN_ACTION_PRIO[proto]
        rule = {'ethertype': IPv4,
                'direction': 'egress',
                'protocol': proto,
                'port_range_min': 10,
                'port_range_max': 100}
        flow_call_list = []
        for port in self.mport:
            flow_call_list.append(
                mock.call(actions=self._learn_egress_actions(proto,
                                rule['ethertype'], priority),
                          dl_src=self.fake_port_1['mac_address'],
                          nw_src=FAKE_IP[IPv4],
                          priority=30,
                          proto=self._write_proto(IPv4, proto),
                          table=OF_EGRESS_TABLE,
                          udp_dst=port))
        self._test_rules([rule], FAKE_SGID, flow_call_list, any_order=True)

    def test_filter_ipv6_egress_udp_mport(self):
        proto = 'udp'
        priority = PROTOCOLS_LEARN_ACTION_PRIO[proto]
        rule = {'ethertype': IPv6,
                'direction': 'egress',
                'protocol': proto,
                'port_range_min': 10,
                'port_range_max': 100}
        flow_call_list = []
        for port in self.mport:
            flow_call_list.append(
                mock.call(actions=self._learn_egress_actions(proto,
                                rule['ethertype'], priority),
                          dl_src=self.fake_port_1['mac_address'],
                          ipv6_src=FAKE_IP[IPv6],
                          priority=30,
                          proto=self._write_proto(IPv6, proto),
                          table=OF_EGRESS_TABLE,
                          udp_dst=port))
        self._test_rules([rule], FAKE_SGID, flow_call_list, any_order=True)

    def test_filter_ipv4_egress_udp_mport_prefix(self):
        proto = 'udp'
        priority = PROTOCOLS_LEARN_ACTION_PRIO[proto]
        prefix = FAKE_PREFIX[IPv4]
        rule = {'ethertype': IPv4,
                'direction': 'egress',
                'protocol': proto,
                'port_range_min': 10,
                'port_range_max': 100,
                'dest_ip_prefix': prefix}
        flow_call_list = []
        for port in self.mport:
            flow_call_list.append(
                mock.call(actions=self._learn_egress_actions(proto,
                                rule['ethertype'], priority),
                          dl_src=self.fake_port_1['mac_address'],
                          nw_dst=prefix,
                          nw_src=FAKE_IP[IPv4],
                          priority=30,
                          proto=self._write_proto(IPv4, proto),
                          table=OF_EGRESS_TABLE,
                          udp_dst=port))
        self._test_rules([rule], FAKE_SGID, flow_call_list, any_order=True)

    def test_filter_ipv6_egress_udp_mport_prefix(self):
        proto = 'udp'
        priority = PROTOCOLS_LEARN_ACTION_PRIO[proto]
        prefix = FAKE_PREFIX[IPv6]
        rule = {'ethertype': IPv6,
                'direction': 'egress',
                'protocol': proto,
                'port_range_min': 10,
                'port_range_max': 100,
                'dest_ip_prefix': prefix}
        flow_call_list = []
        for port in self.mport:
            flow_call_list.append(
                mock.call(actions=self._learn_egress_actions(proto,
                                rule['ethertype'], priority),
                          dl_src=self.fake_port_1['mac_address'],
                          ipv6_dst=prefix,
                          ipv6_src=FAKE_IP[IPv6],
                          priority=30,
                          proto=self._write_proto(IPv6, proto),
                          table=OF_EGRESS_TABLE,
                          udp_dst=port))
        self._test_rules([rule], FAKE_SGID, flow_call_list, any_order=True)
