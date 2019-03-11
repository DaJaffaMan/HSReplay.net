import _ from "lodash";
import React from "react";
import { WithTranslation, withTranslation } from "react-i18next";
import {
	VictoryArea,
	VictoryAxis,
	VictoryChart,
	VictoryLabel,
	VictoryScatter,
	VictoryVoronoiContainer,
} from "victory";
import { getChartMetaData } from "../../helpers";
import { RenderData } from "../../interfaces";
import ChartHighlighter from "./ChartHighlighter";
import WinLossGradient from "./gradients/WinLossGradient";

interface Props extends WithTranslation {
	data?: RenderData;
	opponentClass?: string;
	widthRatio?: number;
	premiumLocked?: boolean;
}

class WinrateByTurnLineChart extends React.Component<Props> {
	constructor(props: Props, context?: any) {
		super(props, context);
		this.state = {
			cursorPos: null,
		};
	}

	public render(): React.ReactNode {
		const { t } = this.props;
		const width = 150 * (this.props.widthRatio || 3);
		const renderData = this.props.premiumLocked
			? this.mockData
			: this.props.data;

		const series = renderData.series.find(
			s =>
				s.name === "winrates_by_turn" &&
				(this.props.opponentClass === "ALL" ||
					s.metadata["opponent_class"] === this.props.opponentClass),
		);

		const yDomain: [number, number] = [Number.MAX_SAFE_INTEGER, 0];
		renderData.series
			.filter(s => s.name === "winrates_by_turn")
			.forEach(s => {
				const metaData = getChartMetaData(s.data, 50, false, 10);
				yDomain[0] = Math.min(metaData.yDomain[0], yDomain[0]);
				yDomain[1] = Math.max(metaData.yDomain[1], yDomain[1]);
			});

		const metaData = getChartMetaData(series.data, 50, false, 10);

		const yTicks = [50];
		yDomain.forEach(
			value => yTicks.indexOf(value) === -1 && yTicks.push(value),
		);

		const filterId = _.uniqueId("winrate-by-turn-gradient-");
		const blurId = _.uniqueId("winrate-gaussian-blur-");

		const height = 150;
		const fontSize = 8;
		const padding = { left: 40, top: 10, right: 20, bottom: 40 };
		const yCenter = height / 2 - (padding.bottom - padding.top) / 2;

		const chart = (
			<VictoryChart
				height={height}
				width={width}
				domainPadding={{ x: 5, y: 10 }}
				padding={padding}
				domain={{ x: metaData.xDomain, y: yDomain }}
				containerComponent={
					<VictoryVoronoiContainer voronoiDimension="x" />
				}
			>
				<VictoryAxis
					tickCount={series.data.length}
					tickFormat={tick => tick}
					label={t("Turn")}
					style={{
						axisLabel: { fontSize },
						tickLabels: { fontSize },
						grid: { stroke: "lightgray" },
						axis: { visibility: "hidden" },
					}}
				/>
				<VictoryAxis
					dependentAxis
					label={t("Winrate")}
					axisLabelComponent={
						<VictoryLabel
							textAnchor="middle"
							verticalAnchor="middle"
							x={fontSize / 2}
							y={yCenter}
						/>
					}
					scale="linear"
					tickValues={yTicks}
					tickFormat={tick => {
						if (tick === 50) {
							return "50%";
						}
						return metaData.toFixed(tick) + "%";
					}}
					style={{
						axis: { visibility: "hidden" },
						axisLabel: { fontSize },
						grid: {
							stroke: tick =>
								tick === 50 ? "gray" : "lightgray",
						},
						tickLabels: { fontSize },
					}}
				/>
				<VictoryScatter
					data={series.data}
					symbol="circle"
					scale="linear"
					size={1}
					labels={d => t("Turn {x}\n{y}%", { x: d.x, y: d.y })}
					labelComponent={
						<ChartHighlighter xCenter={metaData.xCenter} />
					}
				/>
				<defs>
					<WinLossGradient id={filterId} metadata={metaData} />
				</defs>
				<VictoryArea
					data={series.data.map(p => ({ x: p.x, y: p.y, _y0: 50 }))}
					style={{
						data: {
							fill: `url(#${filterId})`,
							stroke: "black",
							strokeWidth: 0.3,
						},
					}}
					interpolation="monotoneX"
					scale="linear"
				/>
			</VictoryChart>
		);

		return chart;
	}

	readonly mockData: RenderData = {
		series: [
			{
				name: "winrates_by_turn",
				data: [
					{ y: 54, x: 1 },
					{ y: 46, x: 2 },
					{ y: 54, x: 3 },
					{ y: 46, x: 4 },
					{ y: 54, x: 5 },
					{ y: 46, x: 6 },
					{ y: 54, x: 7 },
					{ y: 46, x: 8 },
					{ y: 54, x: 9 },
					{ y: 46, x: 10 },
				],
			},
		],
	};
}

export default withTranslation()(WinrateByTurnLineChart);
