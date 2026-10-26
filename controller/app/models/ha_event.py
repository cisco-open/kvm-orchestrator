# SPDX-License-Identifier: Apache-2.0
#
# Copyright [2026] [Cisco Systems, Inc. and its affiliates]
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""HA Event tracking model."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import Base


class HAEvent(Base):
    __tablename__ = "ha_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    node_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("nodes.id"), nullable=True
    )
    vm_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("vms.id"), nullable=True)
    source_node_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_node_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    trigger: Mapped[str] = mapped_column(
        String(30), nullable=False
    )  # heartbeat_timeout/manual
    result: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # success/failed
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
