import React, { StrictMode } from "react";
import ErrorReporter from "./ErrorReporter";
import { Provider as HearthstoneAccountProvider } from "./utils/hearthstone-account";
import { I18nextProvider } from "react-i18next";
import i18n from "../i18n";
import UserData from "../UserData";
import AdHelper from "../AdHelper";

UserData.create();
AdHelper.create();

export default class Root extends React.Component {
	public render(): React.ReactNode {
		return (
			<StrictMode>
				<I18nextProvider i18n={i18n}>
					<ErrorReporter>
						<HearthstoneAccountProvider>
							{this.props.children}
						</HearthstoneAccountProvider>
					</ErrorReporter>
				</I18nextProvider>
			</StrictMode>
		);
	}
}
