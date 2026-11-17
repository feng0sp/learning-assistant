"""Deterministic teaching calculators; no system/network configuration changes."""
import ipaddress
import math

from pydantic import BaseModel, Field, field_validator


class SubnetInput(BaseModel):
    address: str = Field(min_length=3, max_length=50)


class DelayInput(BaseModel):
    bytes: int = Field(ge=1, le=1000000000)
    mbps: float = Field(gt=0, le=1000000, allow_inf_nan=False)
    distance_km: float = Field(ge=0, le=1000000, allow_inf_nan=False)
    speed: float = Field(gt=0, le=300000000, allow_inf_nan=False)
    hops: int = Field(ge=1, le=100)
    packets: int = Field(ge=1, le=1000000)

    @field_validator('mbps', 'speed')
    @classmethod
    def practical_minimum(cls, value):
        if value < 0.000001:
            raise ValueError('输入过小，请使用至少 0.000001 的数值')
        return value


class CRCInput(BaseModel):
    data: str = Field(pattern=r'^[01]{1,256}$')
    generator: str = Field(pattern=r'^1[01]{0,30}1$')


def subnet(address):
    if '/' not in address:
        raise ValueError('请带上前缀长度，例如 192.168.1.70/26。')
    try:
        interface = ipaddress.IPv4Interface(address.strip())
    except ValueError:
        raise ValueError('请输入有效的 IPv4 地址和前缀，例如 192.168.1.70/26。') from None
    network = interface.network
    special = network.prefixlen >= 31
    first = network.network_address if special else network.network_address + 1
    last = network.broadcast_address if special else network.broadcast_address - 1
    return {'title': str(network), 'steps': [
        f'主机地址：{interface.ip}；掩码：{network.netmask}',
        f'网络地址：{network.network_address}；主机位：32 − {network.prefixlen} = {32-network.prefixlen}',
        f'地址总数：2^{32-network.prefixlen} = {network.num_addresses}',
        f'广播地址：{network.broadcast_address}' if not special else '此处 /31 按点对点端点用途、/32 按单地址用途解释，不设置普通子网广播地址。',
        f'可用范围：{first} ～ {last}；数量：{network.num_addresses if special else network.num_addresses - 2}',
        '普通子网范围计算不检查运营商或系统保留规则，不能据此认定所有地址都可用于真实公网配置。',
    ]}


def delay(value):
    send = value.bytes * 8 / (value.mbps * 1000000)
    propagation = value.distance_km * 1000 / value.speed
    total = (value.packets + value.hops - 1) * send + value.hops * propagation
    assert math.isfinite(total)
    return {'title': f'最后一包完整到达：{total * 1000:.6g} ms', 'steps': [
        '模型：各跳等长、等速率、等分组，存储转发；忽略头部、排队、处理与重传。距离填写的是每一跳。',
        f'每包长度：{value.bytes} Byte × 8 = {value.bytes * 8} bit',
        f'每跳发送：L/R = {send * 1000:.6g} ms',
        f'每跳传播：d/v = {propagation * 1000:.6g} ms',
        f'总时间：(包数 + 跳数 − 1) × 发送 + 跳数 × 传播 = {total * 1000:.6g} ms',
        f'单跳单向传播管道容量：R × d/v = {value.mbps * 1000000 * propagation:.6g} bit。不是按 RTT 计算的 TCP 窗口。',
    ], 'total_ms': total * 1000}


def crc(value):
    degree = len(value.generator) - 1
    dividend = list(map(int, value.data + '0' * degree))
    polynomial = list(map(int, value.generator))
    steps = [f'生成式次数 r={degree}；在原数据后补 {degree} 个零。', '被除数：' + ''.join(map(str, dividend))]
    operations = 0
    for i in range(len(value.data)):
        if dividend[i]:
            for j, bit in enumerate(polynomial):
                dividend[i + j] ^= bit
            operations += 1
            if operations <= 16:
                steps.append(f'从第 {i + 1} 位对齐异或：' + ''.join(map(str, dividend)))
    if operations > 16:
        steps.append(f'共 {operations} 次异或，仅展示前 16 次。')
    remainder = ''.join(map(str, dividend[-degree:]))
    codeword = value.data + remainder
    steps.extend(['余数（保留前导零）：' + remainder, '发送序列：' + codeword,
                  '这是教学用裸模二多项式除法，不包含具体 CRC 标准的初值、反射和最终异或参数。检错不等于加密或纠错。'])
    return {'title': 'CRC 余数：' + remainder, 'steps': steps, 'remainder': remainder, 'codeword': codeword}
