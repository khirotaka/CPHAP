from typing import Tuple, List

import torch
import torch.nn as nn
from torchscan import crawl_module
from quicksom.som import SOM
from sklearn.decomposition import PCA

from .functions import find_t, han, generate_indices_for_hap


class CNN(nn.Module):
    def __init__(
        self, in_features: int, mid_features: int, n_class: int, depth: int = 1
    ):
        super(CNN, self).__init__()
        self.conv = nn.ModuleList([nn.Conv1d(in_features, mid_features, kernel_size=3)])
        self.depth = depth
        tmp = [
            nn.Conv1d(mid_features, mid_features, kernel_size=3)
            for _ in range(depth - 1)
        ]
        self.conv.extend(tmp)
        self.pool = nn.AdaptiveMaxPool1d(1)
        self.fc = nn.Linear(mid_features, n_class)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        batch = x.shape[0]

        for layer in self.conv:
            x = layer(x)

        out = self.pool(x)
        out = out.reshape(batch, -1)
        return self.fc(out)


def calculate_rf(model: CNN, dummy_in: Tuple[int, int]) -> List[int]:
    """
    与えられたPyTorchモデル(上記のCNNのインスタンスである必要がある)の `conv` 部分の受容野サイズを計算。
    List に各層のサイズを記録して返す関数。

    Args:
        model: CNN
        dummy_in: CNNに入力するデータの形状。(チャンネル数×時系列の長さ) のタプル

    Returns:
        [第1層のRF, 第2層のRF, 第3層のRF, ..., 第N層のRF]

    """
    rf = []
    module_info = crawl_module(model, dummy_in)
    info = module_info["layers"][1 : model.depth + 1]

    for i in info:
        rf.append(int(i["rf"]))

    return rf


def calculate_thresholds(model: CNN, data: torch.Tensor) -> List[torch.Tensor]:
    """
    与えられたデータを基にPyTorchモデルのレイヤー毎の閾値を計算。
    リストにして返す。

    Args:
        model: PyTorch Model (CNN)
        data: dataset [N, C, L]

    Returns:

    """
    results = []
    for layer in model.conv:
        data = layer(data)
        results.append(find_t(data))

    return results


def hap_core(
    hap_lists: List[List],
    model: CNN,
    x: torch.Tensor,
    j: int,
    k: int,
    thresholds: List[torch.Tensor],
    receptive_field: List[int],
):
    """
    与えられたデータを元にCNNのレイヤーj のチャンネルk の活性化値(A_jk)を計算、
    HAN_{j,k}を求め、RFサイズを元に入力データの中から対応する部分列を切り出し、hap_lists[j]に書き込む。

    Args:
        hap_lists: [list() for _ in range(CNN Depth)]
        model: PyTorch model (CNN)
        x: torch.Tensor(shape == [C, L])
        j: 対象の層番号
        k: 対象のチャンネル番号
        thresholds: `calculate_thresholds` の結果
        receptive_field: `calculate_rf` の結果

    Returns:
        - 部分列を格納した hap_lists
        - 入力データの内、活性化した部分に対応するindex番号のリスト
    """
    data = x.unsqueeze(0)

    for count, layer in enumerate(model.conv):
        data = layer(data)
        if count == j:
            break

    han_jk, max_size = han(data, channel=k, thresholds=thresholds[j])
    rf_j = receptive_field[j]
    tmp = generate_indices_for_hap(han_jk, rf_j, max_size)
    if len(tmp) != 0:
        hap_lists[j].append(x[:, tmp])

    return hap_lists, tmp


def train_som(data: torch.Tensor, map_size: Tuple[int, int], epochs: int) -> SOM:
    """

    Args:
        data: [N, sub_seq_len]
        map_size: ex, (8, 8)
        epochs:

    Returns:

    """
    device = data.device
    data = PCA(n_components=0.95).fit_transform(data.cpu().numpy()).astype(np.float32)
    data = torch.tensor(data, dtype=torch.float32, device=device)
    columns = map_size[0]
    rows = map_size[1]
    som = SOM(columns, rows, data.shape[-1], niter=epochs, device=device)
    som.fit(data, print_each=epochs * 100)

    return som


def predict_cluster(trained_som: SOM, data: torch.Tensor) -> torch.Tensor:
    c = trained_som.predict(data)[0]
    return c