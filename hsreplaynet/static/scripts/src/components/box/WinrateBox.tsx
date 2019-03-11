import React from "react";
import { Trans, WithTranslation, withTranslation } from "react-i18next";
import { AutoSizer } from "react-virtualized";
import { toDynamicFixed, winrateData } from "../../helpers";
import { LoadingStatus } from "../../interfaces";
import WinrateLineChart from "./WinrateLineChart";
import { formatNumber } from "../../i18n";

interface Props extends WithTranslation {
	chartData?: any;
	games?: number;
	href: string;
	onClick?: () => void;
	winrate?: number;
	status?: LoadingStatus;
}

class WinrateBox extends React.Component<Props> {
	public render(): React.ReactNode {
		const { t } = this.props;
		let chart = null;
		if (this.props.chartData) {
			chart = (
				<AutoSizer disableHeight>
					{({ width }) => (
						<WinrateLineChart
							data={this.props.chartData}
							height={50}
							width={width}
						/>
					)}
				</AutoSizer>
			);
		}

		let content = null;
		if (
			this.props.winrate !== undefined &&
			this.props.games !== undefined
		) {
			const wrData = winrateData(50, this.props.winrate, 3);
			const winrate = `${toDynamicFixed(this.props.winrate, 2)}%`;
			const gameCount = formatNumber(this.props.games);
			content = (
				<Trans
					defaults="<0>{winrate}</0> <1>over {gameCount} games</1>"
					components={[
						<h1 style={{ color: wrData.color }} key={0}>
							0
						</h1>,
						<h3 key={1}>1</h3>,
					]}
					tOptions={{
						winrate,
						gameCount,
					}}
				/>
			);
		} else if (
			this.props.status === LoadingStatus.NO_DATA ||
			this.props.status === LoadingStatus.PROCESSING
		) {
			content = t("Please check back later");
		}

		return (
			<div className="col-xs-12 col-sm-6 col-md-4 col-lg-4">
				<a
					className="box winrate-box"
					href={this.props.href}
					onClick={event => {
						if (this.props.onClick) {
							event.preventDefault();
							this.props.onClick();
						}
					}}
				>
					<div className="box-title">{t("Winrate")}</div>
					<div className="box-content">{content}</div>
					<div className="box-chart">{chart}</div>
				</a>
			</div>
		);
	}
}

export default withTranslation()(WinrateBox);
